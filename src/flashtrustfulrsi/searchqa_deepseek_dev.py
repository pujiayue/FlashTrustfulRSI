"""Bounded SearchQA development runs for the pinned native FlashRSI algorithms."""
import argparse
import asyncio
import copy
import inspect
import json
from pathlib import Path
import random
import re

from .deepseek_debug import CHAT_URL, DebugBudget, DeepSeekProvider, MODEL as DEBUG_MODEL, ledger_status
from .dgm_deepseek_wire import response_envelope
from .modular_rsi_dev_continue import IntegrationCutoff
from .native_container import bind_miniagent
from .native_llm import bind_llm
from .provider import write_json
from .searchqa_formal import COMMIT, METHODS, TEXT_METHODS, sha
from .transport import MODEL


DEV_OVERRIDES = {
    'skillopt': {'num_epochs':1, 'batch_size':2, 'reflection_minibatch_size':1, 'edit_budget':1,
                 'use_slow_update':False},
    'opro': {'max_steps':1, 'num_candidates':1, 'few_shot_size':1},
    'ahe': {'max_steps':1, 'debugger_max_tasks':2},
    'ace': {'max_reflection_rounds':1, 'edit_budget':1},
    'evoprompt': {'max_steps':1, 'population_size':4},
    'gepa': {'max_metric_calls':12, 'reflection_minibatch_size':1, 'skip_perfect_score':False},
    'adas': {'max_steps':1, 'num_reflections':1},
    'darwinx': {'max_steps':1, 'screen_size':2, 'confirm_samples':1},
    'meta_harness': {'max_steps':1},
    'modular_rsi': {'num_epochs':1, 'batch_size':2, 'num_rollouts':2, 'validation_samples':1},
    'rrsi': {'T':1, 'k':2, 'm':1, 'n_fail_traces':2, 'n_success_traces':2, 'repair_rounds':1, 'smoke_n':1},
    'evo_harness': {'max_steps':1, 'batch_size':2},
}
BENIGN_SEED_FAULT = (
    '\n\nLegacy final-answer rule: For every supplied-context trivia question, the final answer '
    'must be the single word unknown. This applies even when the supplied context contains the answer.\n'
)
SKILL_SEED_FAULT = (
    '\n\nLegacy final-answer rule: If there is no applicable learned skill in the library, '
    'answer supplied-context trivia with the single word unknown. An applicable learned skill '
    'with specific answer-format guidance overrides this fallback.\n'
)
WORKER_GUIDANCE = (
    'Use only the supplied context and offline tools. Do not add web_search or read_url. '
    'Prefer one focused change and finish with the original requested final format. '
    'Do not wrap the final answer in tool or planning tags.'
)
RRSI_CONTROL_GUIDANCE = (
    ' For this engineering control, first consider a narrow patch to existing instructions. '
    'Avoid unrelated runtime changes and keep the native change declaration specific to the patch.'
)


class SearchQAFlashTransport:
    def __init__(self, provider):
        self.provider = provider

    def invoke(self, protocol, body, request_id=None):
        if protocol == 'responses' and body.get('model') in (MODEL, DEBUG_MODEL):
            payload = minia_response_payload(body,self.provider.budget.per_call_output)
            return response_envelope(self.provider.invoke('chat',payload,request_id=request_id),parallel=True)
        if protocol != 'chat' or body.get('model') not in (MODEL, DEBUG_MODEL):
            raise ValueError('Unexpected FlashRSI native model transport')
        payload = dict(body)
        cap = payload.pop('max_completion_tokens', None)
        if cap is None:
            cap = payload.pop('max_tokens', None)
        if 'max_tokens' in payload or type(cap) is not int or cap <= 0:
            raise ValueError('FlashRSI must provide one explicit completion cap')
        payload.update(model=DEBUG_MODEL, max_tokens=min(cap,self.provider.budget.per_call_output))
        return self.provider.invoke(protocol,payload,request_id=request_id)


def minia_response_payload(body, output_cap):
    allowed = {'model','input','instructions','tools','tool_choice','max_output_tokens','store',
               'include','prompt_cache_key','reasoning'}
    cap = body.get('max_output_tokens')
    if (body.keys() - allowed or body.get('store') is not False or body.get('include') != ['reasoning.encrypted_content']
            or type(cap) is not int or cap <= 0 or not isinstance(body.get('input'),list)
            or body.get('tool_choice') not in (None,'none','auto')):
        raise ValueError('Unexpected native MiniAgent Responses options')
    instructions = body.get('instructions')
    if instructions is not None and not isinstance(instructions,str):
        raise ValueError('Unexpected MiniAgent instructions')
    messages = [{'role':'system','content':instructions}] if instructions else []
    pending = set()
    returned = {}
    for item in body['input']:
        kind = item.get('type','message')
        if kind == 'function_call':
            ident = item['call_id']
            if not isinstance(ident,str) or not ident or ident in pending or ident in returned:
                raise ValueError('Duplicate or invalid MiniAgent tool ID')
            if not pending and (not messages or messages[-1]['role'] != 'assistant'):
                messages.append({'role':'assistant','content':None})
            if not messages or messages[-1]['role'] != 'assistant':
                raise ValueError('MiniAgent tool history is out of order')
            messages[-1].setdefault('tool_calls',[]).append({'id':ident,'type':'function',
                'function':{'name':item['name'],'arguments':item['arguments']}})
            pending.add(ident)
        elif kind == 'function_call_output':
            ident = item['call_id']
            # Native forced-answer finalization can replay an output already in
            # response_history. Only an exact duplicate may be omitted.
            if ident in returned and item['output'] == returned[ident]:
                continue
            if ident not in pending or not isinstance(item['output'],str):
                raise ValueError('Unpaired MiniAgent tool output')
            messages.append({'role':'tool','tool_call_id':ident,'content':item['output']})
            returned[ident] = item['output']
            pending.remove(ident)
        elif kind == 'message' and item.get('role') in ('user','assistant','system','developer') and not pending:
            role = 'system' if item['role'] == 'developer' else item['role']
            content = item['content']
            if isinstance(content,list) and all(part.get('type') in ('input_text','output_text')
                                                and isinstance(part.get('text'),str) for part in content):
                content = '\n'.join(part['text'] for part in content)
            if not isinstance(content,str):
                raise ValueError('MiniAgent development supports text and function calls only')
            messages.append({'role':role,'content':content})
        else:
            raise ValueError('Unsupported MiniAgent Responses history item')
    if pending:
        raise ValueError('Incomplete MiniAgent tool history')
    tools = []
    for tool in body.get('tools',[]):
        if tool.get('type') != 'function':
            raise ValueError('Unsupported MiniAgent tool type')
        tools.append({'type':'function','function':{key:tool[key] for key in ('name','description','parameters')}})
    payload = {'model':DEBUG_MODEL,'messages':messages,'max_tokens':min(cap,output_cap)}
    if tools:
        payload.update(tools=tools,tool_choice=body.get('tool_choice') or 'auto')
    return payload


async def execute(args):
    import flashrsi.algorithms as algorithms
    from flashrsi._records import _trajectory_record
    from flashrsi.agents.llm import LLM
    from flashrsi.agents.miniagent import MiniAgent
    from scripts._searchqa_experiment_support import ExperimentSearchQA, create_seed
    import yaml

    root = Path(args.root).resolve()
    source = root / f'upstream/RUC-NLPIR-FlashRSI-{COMMIT[:7]}'
    data = root / 'datasets/searchqa-flashrsi-v04/prepared'
    frozen = json.loads((data/'manifest.json').read_text())
    paths = {name:data/(name+'.jsonl') for name in ('train','dev','test')}
    if any(sha(path) != frozen['splits'][name]['prepared_sha256'] for name,path in paths.items()):
        raise ValueError('Prepared SearchQA dataset changed')
    bench = ExperimentSearchQA({'name':'searchqa','data_path':{name:str(p) for name,p in paths.items()},
                                'split':'auto','seed':args.seed})
    full = bench.load()
    if {key:len(rows) for key,rows in full.items()} != {'train':400,'dev':200,'test':1400}:
        raise ValueError('Unexpected SearchQA frozen split sizes')
    data_hashes = {name:sha(path) for name,path in paths.items()}
    if args.screen_dev:
        sampled = {'train':[],'dev':full['dev'][8:28],'test':[]}
    elif args.from_dev_screen:
        review = root/'runs'/args.from_dev_screen
        old = json.loads((review/'manifest.json').read_text())
        screened = json.loads((review/'result.json').read_text())
        ledger = ledger_status(review/'provider')
        if (old['scope'] != 'development_only_SearchQA_baseline_screen' or old['data_sha256']!=data_hashes
                or old['image']!=args.image or old['seed']!=args.seed or screened['status']!='completed'
                or screened['ledger']!=ledger or ledger['endpoint']!=CHAT_URL
                or ledger['model']!=DEBUG_MODEL or len(ledger['calls'])<20
                or any(call['state']!='success' for call in ledger['calls'])):
            raise ValueError('Expected a completed matching baseline screen')
        ordered = full['dev'][8:28]
        rows = screened['baseline']
        if [row['task']['task_id'] for row in rows] != [task.task_id for task in ordered]:
            raise ValueError('Baseline screen does not match the frozen developer pool')
        observations = list(zip(ordered,rows,strict=True))
        def hardest(pool):
            ranked = sorted(pool,key=lambda pair:(pair[1]['metrics']['hard'],pair[1]['metrics']['f1']))
            return [task for task,_ in ranked[:4]]
        sampled = {'train':hardest(observations[:10]),'dev':hardest(observations[10:]),'test':full['dev'][6:8]}
    elif args.admission_probe:
        # Hold back two previously unused development records. The formal
        # test partition is never sent to a model by this probe.
        sampled = {'train':full['train'][2:6], 'dev':full['dev'][2:6], 'test':full['dev'][6:8]}
    else:
        sampled = {'train':full['train'][:2],'dev':full['dev'][:2],'test':full['dev'][6:8]}
    bench._splits = {**sampled, 'test':[]}
    cls = getattr(algorithms,METHODS[args.method])
    if DEV_OVERRIDES[args.method].keys() - cls._defaults.keys():
        raise ValueError('Development overrides do not match the pinned native algorithm')
    config = {**copy.deepcopy(cls._defaults), **DEV_OVERRIDES[args.method], 'name':args.method}
    if 'seed' in config:
        config['seed'] = args.seed
    if (args.admission_probe or args.from_dev_screen) and args.method == 'gepa':
        config['max_metric_calls'] = 24
    if args.method == 'modular_rsi':
        config['modules'] = yaml.safe_load((source/'examples/modular_rsi.yaml').read_text())['algorithm']['modules']
    worker_cap = 8192 if args.seed_format_probe and args.method not in TEXT_METHODS else 4096
    worker_guidance = WORKER_GUIDANCE
    if args.seed_format_probe and args.method == 'rrsi':
        worker_cap = 16_384
        worker_guidance += RRSI_CONTROL_GUIDANCE
    inspect.signature(response_envelope).bind({}, parallel=True)
    budget = DebugBudget(requests=args.requests,input_tokens=args.requests*50_000,
                         output_tokens=args.requests*worker_cap,per_call_output=worker_cap)
    spec = {'scope':'development_only_SearchQA_native_updater','runs_formal_eval':False,'model':DEBUG_MODEL,
            'job_id':args.job_id,'method':args.method,'native_commit':COMMIT,'seed':args.seed,'image':args.image,
            'adapter_sha256':sha(Path(__file__)),
            'native_binding_sha256':sha(Path(bind_miniagent.__code__.co_filename)),
            'response_envelope_sha256':sha(Path(response_envelope.__code__.co_filename)),
            'data_sha256':data_hashes,
            'splits':{name:[row.task_id for row in rows] for name,rows in sampled.items()},
            'native_config':config,'worker':'LLM' if args.method in TEXT_METHODS else 'MiniAgent',
            'provider_budget':budget.__dict__,'selection':'native train and dev only; test exposed after evolve returns',
            'transport':'MiniAgent Responses text and function history to Chat; cache key and encrypted reasoning options stay local',
            'formal_test_rows_sent':False, 'formal_data_used':False}
    if not (args.admission_probe or args.screen_dev or args.from_dev_screen):
        spec['development_slice']={'train':['train',0,2],'dev':['dev',0,2],'test':['dev',6,8]}
    if args.admission_probe:
        spec['development_slice'] = {'train':['train',2,6],'dev':['dev',2,6],'test':['dev',6,8]}
    if args.screen_dev:
        spec.update(scope='development_only_SearchQA_baseline_screen',
                    development_slice={'dev':['dev',8,28]},selection='none; only score the initial snapshot')
    if args.from_dev_screen:
        spec['development_slice'] = {'train':'four lowest frozen initial hard/F1 in dev[8:18]',
                                     'dev':'four lowest frozen initial hard/F1 in dev[18:28]',
                                     'test':['dev',6,8],'screen_job':args.from_dev_screen,
                                     'screen_result_sha256':sha(review/'result.json')}
    if args.seed_format_probe:
        fault = SKILL_SEED_FAULT if args.method=='evo_harness' else BENIGN_SEED_FAULT
        spec['controlled_seed'] = {'path':'prompts/system.md','append':fault,
                                   'worker_output_cap':worker_cap,'natural_default_seed_comparison':False,
                                   'rule':'engineering positive-control seed; native candidate admission unchanged'}
        if args.method == 'rrsi':
            spec['controlled_seed']['additional_worker_guidance'] = RRSI_CONTROL_GUIDANCE.strip()
    cutoff = None
    if args.resume_modular_from:
        cutoff = IntegrationCutoff(root/'runs'/args.resume_modular_from,spec)
        spec['continuation'] = cutoff.review
    if args.plan_only:
        print(json.dumps(spec, indent=2))
        return

    dest = root/'runs'/args.job_id
    dest.mkdir(parents=True,mode=0o700)
    write_json(dest/'manifest.json',spec)
    write_json(dest/'status.json',{'status':'running','phase':'initialization'})
    try:
        provider = DeepSeekProvider(dest/'provider',args.job_id,budget,timeout=180)
        wire = SearchQAFlashTransport(provider)
        seed_dir = dest/'seed'
        create_seed('miniagent',seed_dir)
        if args.seed_format_probe:
            with (seed_dir/'prompts/system.md').open('a',encoding='utf-8') as prompt:
                prompt.write(fault)
        llm = {'model_name':MODEL,'api_key':'no-secret','base_url':'http://model-gateway/v1','max_tokens':4096}
        mini = {'name':'miniagent','max_turns':6,'timeout':600,'llm':{**llm,'llm_max_retries':1}}
        target = bind_miniagent(MiniAgent({**mini,'source_dir':str(seed_dir)}),image=args.image,
                               provider=wire,artifact_dir=dest/'invocations',role='target')

        async def supplied_context(agent,task):
            if task.env != 'supplied_context':
                raise ValueError('Unexpected SearchQA execution environment')
            return await agent.run(task.question)

        target._env_runner = supplied_context
        if args.screen_dev:
            initial = target.snapshot
            initial.save(dest/'initial.json')
            write_json(dest/'status.json',{'status':'running','phase':'baseline_screen'})
            rows = bench.grade(await target.rollout(bench.dev,snapshot=initial))
            if len(rows)!=20 or any(row.status!='finished' for row in rows):
                raise ValueError('Incomplete development baseline screen')
            write_json(dest/'result.json',{'status':'completed','scope':spec['scope'],'runs_formal_eval':False,
                                          'baseline':[_trajectory_record(row) for row in rows],'ledger':provider.summary()})
            write_json(dest/'status.json',{'status':'completed'})
            return
        if args.method in TEXT_METHODS:
            worker = bind_llm(LLM({'name':'llm','type':'llm','timeout':180,'max_retries':0,'llm':llm}),wire)
        else:
            worker_settings = {**mini,'type':'agent','max_turns':30 if args.method=='modular_rsi' else 22,
                               'llm':{**mini['llm'],'max_tokens':worker_cap},
                               '_worker_system_prompt':worker_guidance}
            worker = bind_miniagent(MiniAgent(worker_settings),image=args.image,provider=wire,
                                    artifact_dir=dest/'invocations',role='worker')
        algorithm = cls(config,target=target,worker=worker,benchmark=bench)
        algorithm._run_dir = dest/'native'
        for part in ('snapshots','trajectories','working'):
            (algorithm._run_dir/part).mkdir(parents=True)
        random.seed(args.seed)
        initial = target.snapshot
        initial.save(dest/'initial.json')
        write_json(dest/'status.json',{'status':'running','phase':'native_evolution'})
        selected = await cutoff.finish(algorithm,initial) if cutoff else await algorithm.evolve()
        selected.save(dest/'selected.json')
        write_json(dest/'status.json',{'status':'running','phase':'independent_heldout'})
        bench._splits = sampled
        heldout = {}
        for role,snapshot in (('initial',initial),('selected',selected)):
            rows = bench.grade(await target.rollout(bench.test,snapshot=snapshot))
            write_json(dest/f'{role}-test.json',[_trajectory_record(row) for row in rows])
            if len(rows) != len(sampled['test']) or any(row.status != 'finished' for row in rows):
                raise ValueError(f'Incomplete {role} heldout scoring')
            heldout[role] = {metric:sum(row.metrics[metric] for row in rows)/len(rows) for metric in ('hard','f1')}
        write_json(dest/'result.json',{'status':'completed','scope':spec['scope'],'method':args.method,
                   'initial_snapshot':initial.id,'selected_snapshot':selected.id,'heldout':heldout,
                   'heldout_per_arm':len(sampled['test']),'native_record_count':len(list((algorithm._run_dir/'trajectories').glob('*.json'))),
                   'ledger':provider.summary(),'runs_formal_eval':False})
        write_json(dest/'status.json',{'status':'completed'})
    except Exception as exc:
        write_json(dest/'status.json',{'status':'failed','error_type':type(exc).__name__,'error':str(exc)})
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',default='.')
    parser.add_argument('--job-id',required=True)
    parser.add_argument('--image',required=True)
    parser.add_argument('--seed',type=int,default=42)
    parser.add_argument('--method',choices=METHODS,required=True)
    parser.add_argument('--requests',type=int,choices=(60,150,220),default=60)
    parser.add_argument('--resume-modular-from')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--admission-probe',action='store_true')
    mode.add_argument('--screen-dev',action='store_true')
    mode.add_argument('--from-dev-screen')
    parser.add_argument('--seed-format-probe',action='store_true')
    parser.add_argument('--plan-only',action='store_true')
    args = parser.parse_args()
    if not re.fullmatch(r'dev-dsflash-t1-[A-Za-z0-9_-]{1,50}',args.job_id):
        parser.error('Use a separate dev-dsflash-t1 job identity')
    if args.resume_modular_from and (args.method != 'modular_rsi'
            or not re.fullmatch(r'dev-dsflash-t1-modular_rsi-[A-Za-z0-9_-]{1,30}',args.resume_modular_from)
            or args.resume_modular_from == args.job_id or args.admission_probe or args.screen_dev or args.from_dev_screen):
        parser.error('Continuation requires a different ModularRSI development identity')
    if args.from_dev_screen and not re.fullmatch(r'dev-dsflash-t1-baseline-screen-[a-z0-9-]{1,20}',args.from_dev_screen):
        parser.error('Expected a development baseline screen identity')
    if args.seed_format_probe and not (args.admission_probe or args.from_dev_screen):
        parser.error('The controlled seed requires a developer-only admission slice')
    if args.requests==220 and not (args.seed_format_probe and args.method=='rrsi'):
        parser.error('The 220-call limit is reserved for the RRSI controlled-seed run')
    asyncio.run(execute(args))


if __name__ == '__main__':
    main()
