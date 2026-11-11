"""Formal SearchQA source block: original FlashRSI algorithms on all 2,000 rows.

This is one source block of T1, not completion of its transfer/environment grid.
"""
import argparse,asyncio,copy,fcntl,hashlib,json,random,time
from pathlib import Path
from .native_container import bind_miniagent
from .native_llm import bind_llm
from .native_spool import NativeSpoolProvider
from .provider import write_json

METHODS={'skillopt':'SkillOpt','opro':'OPRO','ahe':'AHE','ace':'ACE','evoprompt':'EvoPrompt','gepa':'GEPA',
    'adas':'ADAS','darwinx':'DarwinX','meta_harness':'MetaHarness','modular_rsi':'ModularRSI','rrsi':'RRSI','evo_harness':'EvoHarness'}
TEXT_METHODS={'skillopt','opro','ace','evoprompt','gepa'}
COMMIT='8567c658f52b73ddcc75cb842d9e8f7f88bd1e6f'


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def construct(root,dest,method,seed,image):
    import flashrsi.algorithms as algorithms
    from flashrsi.agents.miniagent import MiniAgent
    from flashrsi.agents.llm import LLM
    from scripts._searchqa_experiment_support import ExperimentSearchQA,create_seed
    data=root/'datasets/searchqa-flashrsi-v04/prepared';manifest=json.loads((data/'manifest.json').read_text())
    for split,row in manifest['splits'].items():
        if sha(data/(split+'.jsonl'))!=row['prepared_sha256']:raise ValueError('Dataset checksum changed')
    benchmark=ExperimentSearchQA({'name':'searchqa','data_path':{s:str(data/(s+'.jsonl')) for s in ('train','dev','test')},'split':'auto','seed':seed})
    if {k:len(v) for k,v in benchmark.load().items()}!={'train':400,'dev':200,'test':1400}:raise ValueError('Formal denominator changed')
    seed_dir=dest/'seed'
    if not seed_dir.exists():create_seed('miniagent',seed_dir)
    llm={'model_name':'gpt-5.6-sol','api_key':'no-secret','base_url':'http://model-gateway/v1','max_tokens':16384,'llm_max_retries':1}
    target_config={'name':'miniagent','source_dir':str(seed_dir),'max_turns':10,'timeout':1800,'llm':llm}
    provider=NativeSpoolProvider(root/'runs/model-spool',dest.name,timeout=900)
    target=bind_miniagent(MiniAgent(target_config),image=image,provider=provider,artifact_dir=dest/'invocations',role='target')
    async def supplied_context(agent,task):
        if task.env!='supplied_context':raise ValueError('Unexpected benchmark environment')
        return await agent.run(task.question)
    target._env_runner=supplied_context
    if method=='initial':return benchmark,target,None,manifest
    cls=getattr(algorithms,METHODS[method]);config=copy.deepcopy(cls._defaults);config['name']=method
    if 'seed' in config:config['seed']=seed
    if method=='ace':config['max_reflection_rounds']=1  # Native requirement for environment-tagged tasks.
    if method=='modular_rsi':
        import yaml
        up=next((root/'upstream').glob('RUC-NLPIR-FlashRSI-*'))
        config['modules']=yaml.safe_load((up/'examples/modular_rsi.yaml').read_text())['algorithm']['modules']
    if method in TEXT_METHODS:
        worker_config={'name':'llm','type':'llm','timeout':900,'max_retries':0,
            'llm':{'model_name':'gpt-5.6-sol','api_key':'no-secret','base_url':'http://model-gateway/v1','max_tokens':32768}}
        worker=bind_llm(LLM(worker_config),provider)
    else:
        worker_config={'name':'miniagent','type':'agent','max_turns':30,'timeout':3600,'llm':{**llm,'max_tokens':32768}}
        worker=bind_miniagent(MiniAgent(worker_config),image=image,provider=provider,artifact_dir=dest/'invocations',role='worker')
    return benchmark,target,cls(config,target=target,worker=worker,benchmark=benchmark),manifest


def make_plan(root,phase,image):
    up=next((root/'upstream').glob('RUC-NLPIR-FlashRSI-*'));jobs=[]
    for seed in (42,43,44):
        for method in ['initial',*METHODS]:
            job=f'{phase}-{method.replace("_","-")}-s{seed}'
            dest=root/'runs'/phase/job;dest.mkdir(parents=True,exist_ok=True)
            benchmark,target,algorithm,data=construct(root,dest,method,seed,image)
            jobs.append({'job_id':job,'method':method,'seed':seed,'harness':'mini_agent','source':'searchqa','destination':'searchqa',
                'target':dict(target.config),'worker':dict(algorithm.worker.config) if algorithm else None,
                'algorithm':dict(algorithm.config) if algorithm else None,'initial_snapshot':target.snapshot.id,
                'split_order_sha256':{k:hashlib.sha256(json.dumps([t.task_id for t in v]).encode()).hexdigest() for k,v in benchmark.load().items()}})
    return json.loads(json.dumps({'schema':'flashtrustfulrsi.formal.searchqa.v1','phase_id':phase,'model':'gpt-5.6-sol',
        'scope':'formal_T1_SearchQA_MiniAgent_source_block_only','upstream_commit':COMMIT,'image':image,'job_count':len(jobs),'jobs':jobs,
        'dataset':data,'splits':{k:[t.task_id for t in v] for k,v in benchmark.load().items()},
        'metric':'official SkillOpt hard/EM and F1; FlashRSI ExperimentSearchQA','seeds':[42,43,44],
        'source_files':{str(p.relative_to(up)):sha(p) for section in ('flashrsi/core','flashrsi/algorithms','flashrsi/agents/miniagent','flashrsi/benchmarks/searchqa')
            for p in sorted((up/section).rglob('*')) if p.is_file() and '__pycache__' not in p.parts},
        'source_extra':{name:sha(up/name) for name in ('flashrsi/agents/llm.py','scripts/_searchqa_experiment_support.py','flashrsi/benchmarks/_data.py','flashrsi/benchmarks/_qa.py','examples/modular_rsi.yaml')},
        'adapter_files':{name:sha(Path(__file__).with_name(name)) for name in ('searchqa_formal.py','native_container.py','native_entry.py','native_llm.py','native_spool.py','sandbox.py')},
        'limits_per_job':{'requests':5_000_000,'input_tokens':5_000_000*200000,'output_tokens':5_000_000*32768},
        'selection':'native train/dev rule; test once after final snapshot freezes; separate initial row per seed',
        'deviations':['gpt-5.6-sol for target and worker; no other model',
            'upstream published implementation defaults for each algorithm; not a claim of matching unreleased paper hyperparameters',
            'unequal native algorithm budgets; report actual calls and cost, no cost-matched ranking claim',
            'entire published 2000-question SkillOpt subset, not full SearchQA archive',
            'serial execution, offline resource-limited containers, inference-only Unix socket',
            'ModularRSI uses exact five-module mapping from upstream examples/modular_rsi.yaml; shared files are not function-isolated',
            'ACE max_reflection_rounds=1 as required by native checks for supplied-context environment tasks',
            'FlashRSI ports retain their documented departures from original methods']},default=dict))


async def run_job(root,plan_path,job_id):
    from flashrsi._records import _trajectory_record
    plan=json.loads(plan_path.read_text());item=next(j for j in plan['jobs'] if j['job_id']==job_id)
    up=next((root/'upstream').glob('RUC-NLPIR-FlashRSI-*'))
    for name,digest in plan['adapter_files'].items():
        if sha(Path(__file__).with_name(name))!=digest:raise ValueError('Frozen adapter changed: '+name)
    for name,digest in {**plan['source_files'],**plan['source_extra']}.items():
        if sha(up/name)!=digest:raise ValueError('Frozen source changed: '+name)
    dest=root/'runs'/plan['phase_id']/job_id
    with (dest/'job.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if (dest/'result.json').exists():return
        benchmark,target,algorithm,data=construct(root,dest,item['method'],item['seed'],plan['image'])
        if target.snapshot.id!=item['initial_snapshot'] or data!=plan['dataset']:raise ValueError('Frozen inputs changed')
        if {k:hashlib.sha256(json.dumps([t.task_id for t in v]).encode()).hexdigest() for k,v in benchmark.load().items()}!=item['split_order_sha256']:raise ValueError('Frozen split/order changed')
        if algorithm and json.loads(json.dumps(dict(algorithm.config),default=dict))!=item['algorithm']:raise ValueError('Algorithm recipe changed')
        write_json(dest/'manifest.json',{'plan_sha256':sha(plan_path),**item})
        write_json(dest/'status.json',{'status':'running','phase':'evolution' if algorithm else 'initial-test','started':time.time()})
        random.seed(item['seed']);initial=target.snapshot;initial.save(dest/'initial.json')
        try:
            if algorithm:
                algorithm._run_dir=dest/'native'
                for name in ('snapshots','trajectories','working'):(algorithm._run_dir/name).mkdir(parents=True,exist_ok=True)
                selected=await algorithm.evolve()
            else:selected=initial
            selected.save(dest/'selected.json')
            write_json(dest/'status.json',{'status':'running','phase':'frozen-test','selected_id':selected.id,'time':time.time()})
            records=benchmark.grade(await target.rollout(benchmark.test,snapshot=selected))
            if len(records)!=1400:raise ValueError('Formal test denominator differs')
            write_json(dest/'test.json',[_trajectory_record(r) for r in records])
            result={'status':'completed','scope':plan['scope'],'method':item['method'],'seed':item['seed'],
                'initial_snapshot':initial.id,'selected_snapshot':selected.id,'changed_paths':list(initial.changed_paths(selected)),
                'test_n':1400,'test_errors':sum(r.status!='finished' for r in records),
                'hard':sum(r.metrics['hard'] for r in records)/1400,'f1':sum(r.metrics['f1'] for r in records)/1400,
                'formal_T1_cell_complete':True,'whole_T1_complete':False}
            write_json(dest/'result.json',result);write_json(dest/'status.json',{'status':'completed','finished':time.time()})
        except Exception as exc:
            write_json(dest/'status.json',{'status':'failed','error_type':type(exc).__name__,'error':str(exc),'finished':time.time()});raise


def main():
    p=argparse.ArgumentParser();p.add_argument('action',choices=['plan','job']);p.add_argument('--root',default='.');p.add_argument('--plan',required=True)
    p.add_argument('--phase',default='formal-searchqa-v04');p.add_argument('--image');p.add_argument('--job-id');a=p.parse_args();root=Path(a.root).resolve();path=Path(a.plan).resolve()
    if a.action=='plan':
        plan=make_plan(root,a.phase,a.image)
        if path.exists() and json.loads(path.read_text())!=plan:raise ValueError('Frozen plan differs')
        write_json(path,plan);print(json.dumps({'job_count':plan['job_count'],'test_per_job':1400,'phase':plan['phase_id']}))
    else:asyncio.run(run_job(root,path,a.job_id))


if __name__=='__main__':main()
