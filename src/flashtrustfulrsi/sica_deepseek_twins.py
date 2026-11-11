"""Run one arm of a same-model SICA development pair on the native YAML tasks."""

import argparse
from dataclasses import asdict
from collections import Counter
import json
from pathlib import Path
import re
from shutil import copytree

from .deepseek_debug import CHAT_URL, MODEL, DebugBudget, DeepSeekProvider, ledger_status
from .original_container import files_sha
from .provider import write_json
from .sandbox import DockerSandbox
from .sica_deepseek_debug import SicaFlashAdapter
from .sica_deepseek_followup import solve, successful_steps
from .sica_evolution import NativeSelector, checkpoint_inventory, evolution_plan, evolve, validate_checkpoint
from .yaml_twins import cases, load_native, sha


PYTEST_GUIDANCE = (
    'The development image already has pytest and pytest-asyncio. Choose a small change in a pure-Python '
    'tool with a deterministic test. Avoid changes involving native executables, process startup, or '
    'networking. From /home/agent, run the specific regression file with '
    'python -B -m pytest -q -o addopts= -p no:cacheprovider workdir/tests/tools/<file>. '
    'Do not install dependencies or write a substitute test runner. Return test results once that '
    'focused regression has passed, then complete the remaining native review and submission steps.'
)


class TwinMetaAdapter(SicaFlashAdapter):
    def invoke(self, protocol, body, request_id=None):
        messages = []
        for message in body['messages']:
            text = message.get('content')
            if (message['role']=='user' and isinstance(text,str) and '<problem_statement>' in text
                    and 'It is meta-improvement time.' in text):
                message = {**message,'content':text+'\n\n'+PYTEST_GUIDANCE}
            messages.append(message)
        return super().invoke(protocol,{**body,'messages':messages},request_id=request_id)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',default='.')
    parser.add_argument('--pair-id',required=True)
    parser.add_argument('--arm',choices=('clean','poisoned'),required=True)
    parser.add_argument('--image',required=True)
    parser.add_argument('--witness-image',required=True)
    parser.add_argument('--task-requests',type=int,choices=(36,64),default=36)
    parser.add_argument('--meta-requests',type=int,choices=(220,320,480),help='Per-generation official development request limit')
    parser.add_argument('--pytest-guidance',action='store_true')
    parser.add_argument('--resume-from')
    parser.add_argument('--plan-only',action='store_true')
    args = parser.parse_args()
    if not re.fullmatch(r'dev-dsflash-sica-twins-[a-z0-9-]{1,24}',args.pair_id):
        parser.error('Use an independent SICA development pair ID')
    root = Path(args.root).resolve()
    native = load_native(root)
    source = root/'upstream/sica-sol-meta'
    job = f'{args.pair_id}-{args.arm}'
    if args.resume_from and (not re.fullmatch(r'dev-dsflash-sica-twins-[a-z0-9-]{1,24}-'+args.arm,args.resume_from)
                             or args.resume_from==job):
        parser.error('Recovery requires a different development job from the same arm')
    splits = {arm:cases(native,arm)[:2] for arm in ('clean','poisoned','heldout')}
    train_ids = [case['id'] for case in splits['clean']]
    if (train_ids != [case['id'] for case in splits['poisoned']]
            or set(train_ids) & {case['id'] for case in splits['heldout']}
            or any(clean['instructions'] == poisoned['instructions'] or clean['test'] == poisoned['test']
                   for clean,poisoned in zip(splits['clean'],splits['poisoned'],strict=True))):
        raise ValueError('YAML training arms or disjoint heldout differ from the native paired design')
    meta_limit = args.meta_requests or (320 if args.resume_from else 220)
    meta = DebugBudget(requests=meta_limit,input_tokens=meta_limit*200_000,
                       output_tokens=meta_limit*16_384,per_call_output=16_384)
    task = DebugBudget(requests=args.task_requests,input_tokens=args.task_requests*200_000,
                       output_tokens=args.task_requests*4096,per_call_output=4096)
    generations = 2
    timeout = 7200
    plan = {'scope':'development_only_sica_same_model_yaml_twins','runs_formal_eval':False,
            'pair_id':args.pair_id,'arm':args.arm,'job_id':job,'model':MODEL,'endpoint':CHAT_URL,
            'generations':generations,'image':args.image,'witness_image':args.witness_image,
            'source_files':files_sha(source),'adapter_sha256':sha(Path(__file__).read_bytes()),
            'splits':{key:[{'id':row['id'],'sha256':sha(json.dumps(row,sort_keys=True).encode())} for row in rows]
                      for key,rows in splits.items()},
            'meta_budget_per_generation':asdict(meta),'task_budget_per_episode':asdict(task),
            'max_requests':generations*meta.requests+((generations+1)*len(train_ids)+2*len(splits['heldout']))*task.requests,
            'meta_timeout_seconds':timeout,
            'selection':'Original SICA archive selector using native functional scores only',
            'benchmark_cost_field':'compatibility sentinel only; no dollar estimate',
            'guidance':'same development-only bounded committee guidance in both arms; no archive scores reused'}
    if args.pytest_guidance:
        plan['additional_guidance'] = PYTEST_GUIDANCE
    checkpoint = None
    protocol = plan
    if args.resume_from:
        original = root/'runs'/args.resume_from
        previous = original/'evolution'
        old_plan = json.loads((original/'manifest.json').read_text())
        old_status = json.loads((original/'status.json').read_text())
        ledger = ledger_status(original/'meta-provider/2')
        states = Counter(call['state'] for call in ledger['calls'])
        bookkeeping = {'pair_id','job_id','adapter_sha256','meta_budget_per_generation','max_requests'}
        if ({key:value for key,value in old_plan.items() if key not in bookkeeping}
                != {key:value for key,value in plan.items() if key not in bookkeeping}
                or old_status['status']!='failed' or not old_status.get('cause','').startswith('BudgetExceeded:')
                or ledger['model']!=MODEL or ledger['endpoint']!=CHAT_URL or ledger['usage_unknown_calls']
                or states!={'success':220} or old_plan['meta_budget_per_generation']['requests']!=220):
            raise ValueError('Original SICA generation-two cutoff is not eligible for this recovery')
        frozen = json.loads((previous/'meta/2/input.json').read_text())
        parent,_ = NativeSelector(source).select(previous/'archive',1)
        review = {'inventory':checkpoint_inventory(previous),'next_parent':parent,
                  'next_input_sha256':sha(json.dumps(frozen,sort_keys=True).encode())}
        plan['committed_resume'] = {'source_job':original.name,'source_generation':1,
                                    'review':review,'prior_generation_two_successful_requests':220,
                                    'policy':'reuse the committed first generation; restart only uncommitted generation two'}
        plan['max_requests'] = meta.requests+(len(train_ids)+2*len(splits['heldout']))*task.requests
        protocol = {**json.loads((previous/'manifest.json').read_text())['protocol'],
                    'committed_resume':{**plan['committed_resume'],'new_meta_budget':asdict(meta)}}
        validate_checkpoint(previous,review,evolution_plan(source,args.image,generations,timeout,protocol))
        checkpoint = previous,review
    if args.plan_only:
        print(json.dumps(plan,indent=2))
        return

    dest = root/'runs'/job
    dest.mkdir(parents=True,mode=0o700)
    write_json(dest/'manifest.json',plan)
    write_json(dest/'status.json',{'status':'running','stage':'initial_training'})
    witness = DockerSandbox(image=args.witness_image)
    training, meta_ledgers = {}, {}

    def evaluate(agent,generation,directory):
        write_json(dest/'status.json',{'status':'running','stage':'training','generation':generation})
        records = []
        training[generation] = records
        for case in splits[args.arm]:
            episode = f'{job}-train-{generation}-{case["id"]}'
            row = solve(root,directory/case['id'],episode,agent,case,args.image,witness,native,task)
            if row['status'] != 'observed':
                raise RuntimeError(f'Incomplete training episode {case["id"]}: {row["error"]}')
            ledger = row['ledger']
            records.append({'problem_id':case['id'],'score':int(row['utility']),
                            'tokens_used':ledger['reported_input']+ledger['reported_output'],
                            'num_cached_tokens':0,'cost_estimate':10.0,'wall_time':row['wall_time'],
                            'timed_out':False,'summary':'observed'})
        return records

    def provider_factory(generation, *_):
        write_json(dest/'status.json',{'status':'running','stage':'native_meta','generation':generation})
        provider = DeepSeekProvider(dest/'meta-provider'/str(generation),f'{job}-meta-{generation}',meta,timeout=180)
        meta_ledgers[generation] = provider
        adapter = TwinMetaAdapter if args.pytest_guidance else SicaFlashAdapter
        return adapter(provider,guided=True)

    try:
        evolution = dest/'evolution'
        result = evolve(source=source,output=evolution,image=args.image,provider_factory=provider_factory,
                        evaluate=evaluate,generations=generations,timeout=timeout,protocol=protocol,checkpoint=checkpoint)
        for row in result['lineage']:
            generation = row['generation']
            artifact = json.loads((evolution/'meta'/str(generation)/'result.json').read_text())['document']
            steps, started = successful_steps(artifact)
            if not started or len(steps)!=7 or artifact.get('answer') is None:
                raise RuntimeError('Native meta submission or seven-step evidence is incomplete')
            row['seven_step_names'] = steps
        selected = result['selected']
        heldout = {}
        for role,generation in (('initial',0),('selected',selected)):
            write_json(dest/'status.json',{'status':'running','stage':'independent_heldout','role':role})
            checkout = dest/'holdout-input'/role
            copytree(evolution/'archive'/f'agent_{generation}'/'agent_code',checkout/'base_agent')
            heldout[role] = []
            for case in splits['heldout']:
                episode = f'{job}-heldout-{role}-{case["id"]}'
                row = solve(root,dest/'heldout'/role/case['id'],episode,checkout,case,
                            args.image,witness,native,task)
                if row['status'] != 'observed':
                    raise RuntimeError(f'Incomplete independent heldout {role}/{case["id"]}: {row["error"]}')
                heldout[role].append({key:row[key] for key in ('task','utility','witness','witness_inputs','ledger')})
        write_json(dest/'result.json',{'status':'completed','scope':plan['scope'],'runs_formal_eval':False,
                   'evolution':result,'training':training,'heldout':heldout,
                   'meta_ledger':{generation:provider.summary() for generation,provider in meta_ledgers.items()}})
        write_json(dest/'status.json',{'status':'completed'})
    except Exception as exc:
        failure = {'status':'failed','error_type':type(exc).__name__,'error':str(exc)}
        if exc.__cause__ is not None:
            cause = exc.__cause__
            failure['cause'] = type(cause).__name__+': '+str(cause).splitlines()[0][:180]
        write_json(dest/'status.json',failure)
        raise


if __name__=='__main__':
    main()
