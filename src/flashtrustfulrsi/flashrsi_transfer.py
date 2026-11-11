"""Evaluate frozen FlashRSI snapshots on a small TriviaQA transfer split."""
import argparse
import asyncio
import inspect
import json
from pathlib import Path
import re

from .flashrsi_native import sha
from .deepseek_debug import CHAT_URL, MODEL as DEBUG_MODEL, DebugBudget, DeepSeekProvider, ledger_status
from .native_container import bind_miniagent
from .native_spool import NativeSpoolProvider
from .provider import write_json
from .searchqa_deepseek_dev import SearchQAFlashTransport, response_envelope
from .transport import MODEL


async def execute(args):
    from flashrsi._records import _trajectory_record
    from flashrsi.agents.miniagent import MiniAgent
    from flashrsi.benchmarks.triviaqa import TriviaQA
    from flashrsi.core.snapshot import Snapshot

    root=Path(args.root).resolve()
    if any(not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',x) for x in (args.job_id,args.source_job)):
        raise ValueError('Invalid job identity')
    source=root/'runs'/args.source_job
    old=json.loads((source/'result.json').read_text())
    if old['status']!='completed':
        raise ValueError('Source must be a completed SearchQA T1 job')
    if args.deepseek_debug:
        inspect.signature(response_envelope).bind({}, parallel=True)
        manifest=json.loads((source/'manifest.json').read_text())
        source_ledger=ledger_status(source/'provider')
        if (manifest['scope']!='development_only_SearchQA_native_updater' or manifest['runs_formal_eval']
                or manifest.get('controlled_seed')):
            raise ValueError('Transfer requires a completed default-seed SearchQA development job')
        if (old.get('ledger')!=source_ledger or source_ledger['model']!=DEBUG_MODEL
                or source_ledger['endpoint']!=CHAT_URL or not source_ledger['requests']
                or any(row['state']!='success' for row in source_ledger['calls'])
                or old['initial_snapshot']==old['selected_snapshot'] or manifest['image']!=args.image):
            raise ValueError('Source model, image, accepted update or ledger is inconsistent')
    elif not old['scope'].startswith('T1_SearchQA'):
        raise ValueError('Source must be a completed SearchQA T1 job')
    paths={role:source/(role+'.json') for role in ('initial','selected')}
    if args.include_rejected:
        rejected=next(row['candidate_id'] for row in old['decisions'] if row['kind']=='admit' and not row['accepted'])
        if not re.fullmatch(r'[0-9a-f]{64}',rejected):
            raise ValueError('Invalid rejected snapshot ID')
        paths['rejected_diagnostic']=source/'native/snapshots'/(rejected+'.json')
    snapshots={role:Snapshot.load(path) for role,path in paths.items()}
    suffix = '_snapshot' if args.deepseek_debug else '_id'
    if any(snapshots[role].id!=old[role+suffix] for role in ('initial','selected')):
        raise ValueError('Source result and snapshot disagree')
    if args.deepseek_debug:
        decisions = [(path, json.loads(path.read_text())) for path in sorted((source/'native/trajectories').glob('*-decision.json'))]
        admitted = {path.name:sha(path) for path, row in decisions
                    if row.get('candidate_id')==snapshots['selected'].id and row.get('accepted') is True}
        if not admitted:
            raise ValueError('Source selected snapshot has no original admission record')
    if args.include_rejected and snapshots['rejected_diagnostic'].id!=rejected:
        raise ValueError('Rejected snapshot and original decision disagree')
    receipt=json.loads(args.data.with_suffix('.manifest.json').read_text())
    if sha(args.data)!=receipt['subset_sha256']:
        raise ValueError('Transfer dataset changed')
    bench=TriviaQA({'name':'triviaqa','data_path':{'test':str(args.data.resolve())},
                   'split':'auto','scoring':'official'})
    splits=bench.load()
    if [task.task_id for task in splits['test']]!=receipt['task_ids']:
        raise ValueError('TriviaQA IDs differ from frozen list')
    config={'name':'miniagent','max_turns':6,'timeout':600,
            'llm':{'model_name':MODEL,'api_key':'no-secret','base_url':'http://model-gateway/v1',
                   'max_tokens':4096,'llm_max_retries':1}}
    if args.deepseek_debug:
        config['source_dir'] = str(source/'seed')
        seed_snapshot = MiniAgent(config).snapshot
        if seed_snapshot.id!=snapshots['initial'].id or seed_snapshot.root_id!=snapshots['selected'].root_id:
            raise ValueError('Seed workspace or selected snapshot root changed before transfer')
    spec={'job_id':args.job_id,'scope':'T1_SearchQA_to_TriviaQA_near_transfer_smoke','model':MODEL,
          'source_job':args.source_job,'source_result_sha256':sha(source/'result.json'),'image':args.image,
          'snapshots':{role:{'id':snapshots[role].id,'sha256':sha(path)} for role,path in paths.items()},
          'data':receipt,'adapter_sha256':sha(Path(__file__)),
          'selection':'keep the original source decision; rejected diagnostic never selects a model',
          'max_requests':len(paths)*len(bench.test)*6}
    if args.deepseek_debug:
        budget = DebugBudget(requests=spec['max_requests'], input_tokens=1_000_000,
                             output_tokens=spec['max_requests']*4096, per_call_output=4096)
        spec.update(scope='development_only_SearchQA_to_TriviaQA_frozen_transfer', runs_formal_eval=False,
                    model=DEBUG_MODEL, endpoint=CHAT_URL, provider_budget=budget.__dict__,
                    source_admission_records=admitted, response_envelope_sha256=sha(Path(response_envelope.__code__.co_filename)))
    if args.plan_only:
        print(json.dumps(spec,indent=2))
        return
    dest=root/'runs'/args.job_id
    dest.mkdir(parents=True)
    write_json(dest/'manifest.json',spec)
    write_json(dest/'status.json',{'status':'running'})
    try:
        if args.deepseek_debug:
            ledger=DeepSeekProvider(dest/'provider',args.job_id,budget,timeout=180)
            provider=SearchQAFlashTransport(ledger)
        else:
            provider=NativeSpoolProvider(root/'runs/model-spool',args.job_id)
        target=bind_miniagent(MiniAgent(config),image=args.image,provider=provider,
                              artifact_dir=dest/'invocations',role='target')

        async def supplied_context(agent,task):
            if task.env!='supplied_context':
                raise ValueError('Unexpected transfer environment')
            return await agent.run(task.question)

        target._env_runner=supplied_context
        results={}
        for role,snapshot in snapshots.items():
            rows=bench.grade(await target.rollout(bench.test,snapshot=snapshot))
            write_json(dest/(role+'-test.json'),[_trajectory_record(row) for row in rows])
            if args.deepseek_debug and (len(rows)!=len(bench.test) or any(r.status!='finished' for r in rows)):
                raise ValueError(f'Incomplete native TriviaQA trajectories for {role}')
            results[role]={'cases':len(rows),'errors':sum(r.status!='finished' for r in rows),
                           'exact_matches':sum(r.metrics['exact_match'] for r in rows),
                           'mean_f1':sum(r.metrics['f1'] for r in rows)/len(rows)}
        result={'status':'completed','model':spec['model'],'scope':spec['scope'],
                'source_selected_changed':snapshots['initial'].id!=snapshots['selected'].id,'results':results}
        if args.deepseek_debug:
            result.update(ledger=ledger.summary(),runs_formal_eval=False)
        write_json(dest/'result.json',result)
        write_json(dest/'status.json',{'status':'completed'})
    except Exception as exc:
        write_json(dest/'status.json',{'status':'failed','error_type':type(exc).__name__,'error':str(exc)})
        raise


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',default='.')
    parser.add_argument('--job-id',required=True)
    parser.add_argument('--source-job',required=True)
    parser.add_argument('--data',required=True,type=Path)
    parser.add_argument('--image',required=True)
    parser.add_argument('--include-rejected',action='store_true')
    parser.add_argument('--deepseek-debug',action='store_true')
    parser.add_argument('--plan-only',action='store_true')
    args = parser.parse_args()
    if args.deepseek_debug and (not args.job_id.startswith('dev-dsflash-t1-transfer-') or args.include_rejected):
        parser.error('DeepSeek transfer requires a separate development identity and excludes rejected candidates')
    asyncio.run(execute(args))


if __name__=='__main__':
    main()
