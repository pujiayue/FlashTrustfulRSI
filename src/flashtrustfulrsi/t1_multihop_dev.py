"""Mini-Agent/OPRO HotpotQA development run and read-only MuSiQue transfer."""

import argparse
import asyncio
import inspect
import json
from pathlib import Path
import re

from .deepseek_debug import CHAT_URL, MODEL as DEBUG_MODEL, DebugBudget, DeepSeekProvider
from .native_container import bind_miniagent
from .native_llm import bind_llm
from .provider import write_json
from .searchqa_deepseek_dev import SearchQAFlashTransport, response_envelope
from .searchqa_formal import COMMIT, sha
from .transport import MODEL


FILES = {'hotpot-train':3, 'hotpot-heldout':2, 'musique-transfer':2}
FORMAT_CONTROL = '\n\nLegacy rule for supplied-context questions: always return the single word unknown, even when the context gives the answer.\n'


async def execute(args):
    from flashrsi._records import _trajectory_record
    from flashrsi.agents.llm import LLM
    from flashrsi.agents.miniagent import MiniAgent
    from flashrsi.algorithms.opro import OPRO
    from flashrsi.benchmarks.hotpotqa import HotpotQA
    from flashrsi.benchmarks.musique import MuSiQue
    from flashrsi.core.snapshot import Snapshot
    from scripts._searchqa_experiment_support import create_seed

    inspect.signature(response_envelope).bind({}, parallel=True)
    root = Path(args.root).resolve()
    data = args.data.resolve()
    manifest = json.loads((data/'manifest.json').read_text())
    if (manifest['scope']!='development_only_t1_multihop_inputs' or manifest['formal_hotpot_test_used']
            or manifest['model_requests'] or set(manifest['files'])!=FILES.keys()):
        raise ValueError('Expected a frozen development-only multihop manifest')
    for role, count in FILES.items():
        entry = manifest['files'][role]
        if entry['file']!=f'{role}.jsonl' or sha(data/entry['file'])!=entry['sha256'] or len(entry['ids'])!=count:
            raise ValueError(f'Frozen slice differs for {role}')
    if any(len({x for row in manifest['files'].values() for x in row[field]}) != sum(FILES.values())
           for field in ('ids','question_sha256')):
        raise ValueError('Train, source holdout and transfer slices must be disjoint')

    def benchmark(cls, key, split):
        entry = manifest['files'][key]
        bench = cls({'name':cls.name,'mode':'context','scoring':'official',
                     'data_path':{split:str(data/entry['file'])},'seed':42})
        splits = bench.load()
        rows = splits[split]
        if (len(rows)!=FILES[key] or {task.task_id for task in rows}!=set(entry['ids'])
                or any(splits[name] for name in splits if name!=split)):
            raise ValueError(f'Native loader changed the frozen membership for {key}')
        expected_questions = dict(zip(entry['ids'], entry['question_sha256'], strict=True))
        if any(task.metadata['question_checksum'] != expected_questions[task.task_id] for task in rows):
            raise ValueError(f'Native question checksum differs for {key}')
        return bench, rows

    algo_config = {**OPRO._defaults,'name':'opro','max_steps':1,'num_candidates':2,'few_shot_size':1}
    budget = DebugBudget(requests=60, input_tokens=2_000_000, output_tokens=245_760, per_call_output=4096)
    llm = {'model_name':MODEL, 'api_key':'no-secret','base_url':'http://model-gateway/v1',
           'max_tokens':4096}
    plan = {'scope':'development_only_T1_HotpotQA_MuSiQue','runs_formal_eval':False,
            'job_id':args.job_id,'native_commit':COMMIT,'harness':'miniagent','image':args.image,
            'model':DEBUG_MODEL,'endpoint':CHAT_URL,'provider_budget':budget.__dict__,
            'dataset_manifest_sha256':sha(data/'manifest.json'),'dataset':manifest,'algorithm':algo_config,
            'selection':'native OPRO promotes on HotpotQA training scores; ties retain the earlier candidate',
            'transfer':'freeze initial and selected; reload into new target with no optimizer or worker',
            'source_mode':'context from pinned fullwiki parquet; no live retrieval or joint support metric',
            'target_mode':'MuSiQue-Ans context; public dev only; original answer EM/F1',
            'controlled_seed':FORMAT_CONTROL if args.format_control else None,
            'adapter_sha256':sha(Path(__file__)),
            'native_binding_sha256':sha(Path(bind_miniagent.__code__.co_filename)),
            'response_envelope_sha256':sha(Path(response_envelope.__code__.co_filename))}
    training, train_tasks = benchmark(HotpotQA, 'hotpot-train', 'train')
    if args.plan_only:
        plan['native_task_order'] = {'hotpot-train':[task.task_id for task in train_tasks]}
        for cls, role in ((HotpotQA,'hotpot-heldout'), (MuSiQue,'musique-transfer')):
            _, tasks = benchmark(cls, role, 'test')
            plan['native_task_order'][role] = [task.task_id for task in tasks]
        print(json.dumps(plan, indent=2))
        return

    dest = root/'runs'/args.job_id
    dest.mkdir(mode=0o700, parents=True)
    write_json(dest/'manifest.json',plan)
    write_json(dest/'status.json',{'status':'running','phase':'native_evolution'})
    try:
        provider = DeepSeekProvider(dest/'provider',args.job_id,budget,timeout=180)
        wire = SearchQAFlashTransport(provider)
        seed = create_seed('miniagent',dest/'seed')
        if args.format_control:
            with (seed/'prompts/system.md').open('a', encoding='utf-8') as stream:
                stream.write(FORMAT_CONTROL)

        async def supplied_context(agent, task):
            if task.env != 'supplied_context':
                raise ValueError('Multihop development run accepts supplied context only')
            return await agent.run(task.question)

        def target(role):
            agent = bind_miniagent(MiniAgent({'name':'miniagent','source_dir':str(seed),'max_turns':6,
                                              'timeout':600,'llm':{**llm,'llm_max_retries':1}}), image=args.image,
                                   provider=wire,artifact_dir=dest/'invocations',role=role)
            agent._env_runner = supplied_context
            return agent

        agent = target('source-train')
        worker = bind_llm(LLM({'name':'llm','type':'llm','timeout':180,'max_retries':0,'llm':llm}),wire)
        algorithm = OPRO(algo_config, target=agent,worker=worker,benchmark=training)
        algorithm._run_dir = dest/'native'
        for part in ('snapshots','trajectories','working'):
            (algorithm._run_dir/part).mkdir(parents=True)
        initial = agent.snapshot
        initial.save(dest/'initial.json')
        selected = await algorithm.evolve()
        selected.save(dest/'selected.json')
        decisions = {path.name:json.loads(path.read_text()) for path in (dest/'native/trajectories').glob('*-decision.json')}
        if (len(decisions)!=1 or not any(row['parent_id']==initial.id and row['candidate_id']==selected.id
                                       and row['accepted']==(initial.id!=selected.id) for row in decisions.values())):
            raise ValueError('Missing native OPRO promotion or no-change evidence')
        checkpoint = {role:{'id':snapshot.id,'sha256':sha(dest/f'{role}.json')}
                      for role,snapshot in (('initial',initial),('selected',selected))}
        write_json(dest/'source-selection.json',{'snapshots':checkpoint,'decisions':decisions,
                   'native_trajectory_sha256':{p.name:sha(p) for p in sorted((dest/'native/trajectories').glob('*.json'))}})

        scores = {}
        for cls, key, phase in ((HotpotQA,'hotpot-heldout','source-heldout'),(MuSiQue,'musique-transfer','frozen-transfer')):
            write_json(dest/'status.json',{'status':'running','phase':phase})
            bench, tasks = benchmark(cls,key,'test')
            fresh = target(phase)
            if fresh.snapshot.id!=checkpoint['initial']['id']:
                raise ValueError('Fresh harness seed no longer matches the original root')
            scores[phase] = {}
            for role, snapshot_meta in checkpoint.items():
                path = dest/f'{role}.json'
                frozen = Snapshot.load(path)
                if sha(path)!=snapshot_meta['sha256'] or frozen.id!=snapshot_meta['id']:
                    raise ValueError(f'Frozen {role} snapshot changed')
                rows = bench.grade(await fresh.rollout(tasks,snapshot=frozen))
                write_json(dest/f'{phase}-{role}.json',[_trajectory_record(row) for row in rows])
                if len(rows)!=len(tasks) or any(row.status!='finished' for row in rows):
                    raise ValueError(f'Incomplete native trajectories for {phase}/{role}')
                scores[phase][role] = {'cases':len(rows),'em':sum(row.metrics['exact_match'] for row in rows)/len(rows),
                                       'f1':sum(row.metrics['f1'] for row in rows)/len(rows)}
        ledger = provider.summary()
        if not ledger['requests'] or ledger['usage_unknown_calls'] or any(row['state']!='success' for row in ledger['calls']):
            raise ValueError('Official model ledger has incomplete receipts')
        if any(sha(dest/f'{role}.json') != item['sha256'] for role,item in checkpoint.items()):
            raise ValueError('Heldout or transfer changed a frozen snapshot')
        write_json(dest/'result.json',{'status':'completed','runs_formal_eval':False,'scores':scores,
                   'source_selection_sha256':sha(dest/'source-selection.json'),'ledger':ledger})
        write_json(dest/'status.json',{'status':'completed'})
    except Exception as exc:
        write_json(dest/'status.json',{'status':'failed','error_type':type(exc).__name__,'error':str(exc)})
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',default='.')
    parser.add_argument('--job-id',required=True)
    parser.add_argument('--data',type=Path,required=True)
    parser.add_argument('--image',required=True)
    parser.add_argument('--plan-only',action='store_true')
    parser.add_argument('--format-control',action='store_true')
    args = parser.parse_args()
    if (not re.fullmatch(r'dev-dsflash-t1-multihop-[a-z0-9-]{1,32}',args.job_id)
            or not re.fullmatch(r'sha256:[0-9a-f]{64}',args.image)):
        parser.error('Expected a development identity and an immutable local image ID')
    asyncio.run(execute(args))


if __name__=='__main__':
    main()
