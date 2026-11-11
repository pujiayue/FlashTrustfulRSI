"""Run FlashRSI's pinned ADAS search with its native MiniAgent inside containers.

All archive/reflection/dev-admission decisions are made by upstream ADAS code.
This is the FlashRSI port, whose documented differences from original ADAS remain.
"""
import argparse,asyncio,hashlib,json,re,time
from pathlib import Path
from .native_container import bind_miniagent
from .native_spool import NativeSpoolProvider
from .provider import write_json


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


async def execute(args):
    from flashrsi.agents.miniagent import MiniAgent
    from flashrsi.algorithms.adas import ADAS
    from flashrsi.benchmarks.searchqa import SearchQA
    from flashrsi._records import _trajectory_record
    root=Path(args.root).resolve();up=next((root/'upstream').glob('RUC-NLPIR-FlashRSI-*'))
    if not re.fullmatch('[a-zA-Z0-9_-]{1,80}',args.job_id):raise ValueError('Invalid job ID')
    data=up/'examples/data/quickstart.jsonl'
    if args.t1_smoke:
        from scripts._searchqa_experiment_support import ExperimentSearchQA
        prepared = root/'datasets/searchqa-flashrsi-v04/prepared'
        data = prepared/'manifest.json'
        hashes = json.loads(data.read_text())['splits']
        paths = {name: prepared/(name+'.jsonl') for name in ('train','dev','test')}
        for name, path in paths.items():
            if sha(path) != hashes[name]['prepared_sha256']:
                raise ValueError('Prepared SearchQA split checksum changed: '+name)
        benchmark = ExperimentSearchQA({'name':'searchqa','data_path':{k:str(v) for k,v in paths.items()},
                                        'split':'auto','seed':42})
        splits = benchmark.load()
        if {k:len(v) for k,v in splits.items()} != {'train':400,'dev':200,'test':1400}:
            raise ValueError('Unexpected SearchQA split sizes')
        benchmark._splits = {name: tasks[:2] for name,tasks in splits.items()}
    else:
        benchmark=SearchQA({'name':'searchqa','data_path':str(data),'split':{'train':.5,'dev':.25,'test':.25},'seed':42})
    benchmark.load()
    target_config={'name':'miniagent','max_turns':6,'timeout':1800,
        'llm':{'model_name':'gpt-5.6-sol','api_key':'no-secret','base_url':'http://model-gateway/v1','max_tokens':16384,'llm_max_retries':1}}
    worker_config={'name':'miniagent','type':'agent','max_turns':30,'timeout':3600,
        'llm':{'model_name':'gpt-5.6-sol','api_key':'no-secret','base_url':'http://model-gateway/v1','max_tokens':32768,'llm_max_retries':1}}
    algorithm_type = ADAS
    if args.t1_smoke:
        import yaml

        class SuppliedContextADAS(ADAS):
            def _commit(self, baseline, directory):
                try:
                    config = yaml.safe_load((directory/'agent.yaml').read_text())
                except (FileNotFoundError, UnicodeDecodeError, yaml.YAMLError) as exc:
                    raise ValueError('Invalid agent.yaml for the supplied-context environment') from exc
                if not isinstance(config, dict) or not isinstance(config.get('tools', []), list):
                    raise ValueError('Invalid agent.yaml tools for the supplied-context environment')
                if any(name in ('web_search', 'read_url') for name in config.get('tools', [])):
                    raise ValueError('Network tools are unavailable in supplied-context evaluation')
                return super()._commit(baseline, directory)

        algorithm_type = SuppliedContextADAS
        worker_config['_worker_system_prompt'] = (
            'Evaluation provides the search snippets in the prompt. The sandbox has no network. '
            'Do not add web_search or read_url to agent.yaml.'
        )
    algorithm_config={'name':'adas','max_steps':1 if args.t1_smoke else 2,'num_reflections':1 if args.t1_smoke else 2}
    dest=root/'runs'/args.job_id
    provider=NativeSpoolProvider(root/'runs/model-spool',args.job_id,timeout=900)
    target=bind_miniagent(MiniAgent(target_config),image=args.image,provider=provider,artifact_dir=dest/'invocations',role='target')
    async def supplied_context(agent,task):
        if task.env!='supplied_context':raise ValueError('No declared environment integration for '+str(task.env))
        return await agent.run(task.question)
    target._env_runner=supplied_context
    worker=bind_miniagent(MiniAgent(worker_config),image=args.image,provider=provider,artifact_dir=dest/'invocations',role='worker')
    algorithm=algorithm_type(algorithm_config,target=target,worker=worker,benchmark=benchmark)
    manifest={'job_id':args.job_id,'model':'gpt-5.6-sol','scope':'native_FlashRSI_ADAS_MiniAgent_quickstart_integration_not_formal_T1',
        'upstream_commit':'8567c658f52b73ddcc75cb842d9e8f7f88bd1e6f','image':args.image,
        'target':target_config,'worker':worker_config,'algorithm':dict(algorithm.config),
        'data_sha256':sha(data),'splits':{k:[t.task_id for t in v] for k,v in benchmark.load().items()},
        'adapter_files':{n:sha(Path(__file__).with_name(n)) for n in ('flashrsi_native.py','native_container.py','native_entry.py','native_spool.py','sandbox.py')},
        'native_files':{str(p.relative_to(up)):sha(p) for section in ('core','algorithms/adas','agents/miniagent','benchmarks/searchqa')
            for p in sorted((up/'flashrsi'/section).rglob('*')) if p.is_file() and '__pycache__' not in p.parts},
        'limits':{'model_requests':1000,'input_tokens':100_000_000,'output_tokens':32_768_000},
        'test_policy':'initial and selected snapshots tested only after upstream evolution returns; no test feedback to search',
        'limitations':['bundled 12-question demo, not SearchQA formal data','FlashRSI ADAS is a documented mechanism port, not the original ADAS forward-function implementation',
            'supplied-context environment uses equivalent prompt-only staging with a Unix inference socket instead of upstream network-origin firewall; native search and harness are unchanged']}
    if args.t1_smoke:
        manifest['scope'] = 'T1_SearchQA_ADAS_two_cases_per_split_smoke'
        manifest['dataset_files'] = {k: hashes[k]['prepared_sha256'] for k in paths}
        manifest['limitations'][0] = 'First two cases of each frozen SearchQA split; one native ADAS step'
        manifest['limitations'].append('Supplied-context compatibility gate declines candidates that add unavailable network tools')
    if args.plan_only:print(json.dumps(manifest,indent=2,default=dict));return
    dest.mkdir(parents=True,exist_ok=True)
    if (dest/'manifest.json').exists() and json.loads((dest/'manifest.json').read_text())!=manifest:raise ValueError('Frozen native manifest changed')
    write_json(dest/'manifest.json',manifest)
    if (dest/'result.json').exists():print((dest/'result.json').read_text());return
    write_json(dest/'status.json',{'status':'running','started':time.time()})
    algorithm._run_dir=dest/'native';algorithm._run_dir.mkdir(exist_ok=True)
    for directory in ('snapshots','trajectories','working'):(algorithm._run_dir/directory).mkdir(exist_ok=True)
    initial=target.snapshot;initial.save(dest/'initial.json')
    try:
        selected=await algorithm.evolve();selected.save(dest/'selected.json')
        results={}
        for label,snapshot in [('initial',initial),('selected',selected)]:
            rows=benchmark.grade(await target.rollout(benchmark.test,snapshot=snapshot))
            write_json(dest/(label+'-test.json'),[_trajectory_record(row) for row in rows])
            results[label]={'n':len(rows),'errors':sum(row.status!='finished' for row in rows),
                'exact_match':sum(row.metrics['exact_match'] for row in rows)/len(rows)}
            if args.t1_smoke:
                for metric in ('hard', 'f1'):
                    results[label][metric] = sum(row.metrics[metric] for row in rows)/len(rows)
        decisions=[]
        for p in sorted((dest/'native/trajectories').glob('*decision*.json')):decisions.append(json.loads(p.read_text()))
        result={'status':'completed','scope':manifest['scope'],'initial_id':initial.id,'selected_id':selected.id,
            'changed_paths':list(initial.changed_paths(selected)),'results':results,'decisions':decisions,
            'formal_T1':False,'original_ADAS_code_reproduction':False,'native_FlashRSI_search_executed':True}
        write_json(dest/'result.json',result);write_json(dest/'status.json',{'status':'completed','finished':time.time()})
        print(json.dumps(result),flush=True)
    except Exception as exc:
        write_json(dest/'status.json',{'status':'failed','error_type':type(exc).__name__,'error':str(exc),'finished':time.time()});raise


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',default='.')
    p.add_argument('--job-id',required=True);p.add_argument('--image',required=True);p.add_argument('--plan-only',action='store_true')
    p.add_argument('--t1-smoke', action='store_true')
    asyncio.run(execute(p.parse_args()))


if __name__ == '__main__':
    main()
