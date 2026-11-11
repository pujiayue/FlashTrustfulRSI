"""Native AgentDojo full-suite static controls for formal T4 and T6.

These controls do not claim RSI evolution. All task pairs and structural
exclusions are frozen before inference; learned defenses join as separate jobs.
"""
import argparse,fcntl,hashlib,json,time
from pathlib import Path
from types import SimpleNamespace
from .provider import write_json
from .native_transport import RecordedNativeProvider,MODEL
from .dojo import encode

COMMIT='089ed468cf3ed0322acc66b0211f26d9d90dbf60'
DEFENSES=(None,'repeat_user_prompt','spotlighting_with_delimiting')
SUITES=('banking','slack','travel','workspace')


def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()


def plan(root,phase):
    from agentdojo.task_suite.load_suites import get_suite
    from agentdojo.attacks.baseline_attacks import DirectAttack
    up=next((root/'upstream').glob('ethz-spylab-agentdojo-*'))
    jobs=[];excluded=[];counts={}
    for name in SUITES:
        suite=get_suite('v1.2.2',name);attack=DirectAttack(suite,None)
        pairs=[]
        for uid in sorted(suite.user_tasks):
            for aid in sorted(suite.injection_tasks):
                try:injections=attack.attack(suite.user_tasks[uid],suite.injection_tasks[aid])
                except ValueError as exc:
                    excluded.append({'suite':name,'user_task':uid,'injection_task':aid,'reason':str(exc)});continue
                pairs.append({'user_task':uid,'injection_task':aid,'injections':injections,'kind':'attacked'})
        controls=[{'user_task':uid,'injection_task':None,'injections':{},'kind':'clean'} for uid in sorted(suite.user_tasks)]
        feasibility=[{'user_task':aid,'injection_task':None,'injections':{},'kind':'attacker_goal_feasibility'} for aid in sorted(suite.injection_tasks)]
        counts[name]={'users':len(controls),'goals':len(feasibility),'injectable_pairs':len(pairs)}
        for seed in (42,43,44):
            for defense in DEFENSES:
                for case in controls+feasibility+pairs:
                    row={'suite':name,'seed':seed,'defense':defense,**case}
                    row['job_id']='dojo-'+hashlib.sha256(json.dumps(row,sort_keys=True).encode()).hexdigest()[:20]
                    jobs.append(row)
    return {'schema':'flashtrustfulrsi.formal.dojo.v1','phase_id':phase,'model':MODEL,
        'scope':'formal_T4_AgentDojo_direct_and_T6_static_controls_not_complete_T4_T6',
        'upstream_commit':COMMIT,'suite_version':'v1.2.2','counts':counts,'seeds':[42,43,44],
        'max_tool_iterations':15,'native_empty_output_attempts':3,'max_output_tokens':8192,'temperature':0,
        'defenses':DEFENSES,'excluded_structural_pairs':excluded,'job_count':len(jobs),'jobs':jobs,
        'source_files':{str(p.relative_to(up)):sha(p) for p in sorted((up/'src/agentdojo').rglob('*')) if p.is_file() and '__pycache__' not in p.parts},
        'adapter_sha256':sha(Path(__file__)),'transport_sha256':sha(Path(__file__).with_name('native_transport.py')),
        'limits_per_job':{'requests':48,'input_tokens':10_000_000,'output_tokens':48*8192},
        'analysis':{'unit':'user task cluster, paired across conditions; report suites separately',
            'metrics':['clean_utility','attacked_utility','ASR_all_prespecified_pairs','ASR_clean_success_conditioned','execution_error_rate','attacker_goal_feasibility'],
            'interval':'paired user-cluster bootstrap; three seed values separately',
            'error_policy':'retain failed pairs in denominator; never turn transport/turn-limit failures into defense success'},
        'deviations':['gpt-5.6-sol in every LLM role','explicit output cap 8192 and seed',
            'no automatic network retries; original tools, prompts, formatting, defenses, and scorer',
            'direct attack only; other attack permissions and learned RSI assets are separate blocks']}


def run_job(root,plan_path,job_id):
    import openai
    from openai.types.chat import ChatCompletion
    from agentdojo.agent_pipeline.agent_pipeline import AgentPipeline,PipelineConfig
    from agentdojo.agent_pipeline.llms import openai_llm
    from agentdojo.task_suite.load_suites import get_suite
    from agentdojo.logging import OutputLogger,TraceLogger
    frozen=json.loads(plan_path.read_text());case=next(j for j in frozen['jobs'] if j['job_id']==job_id)
    if sha(Path(__file__))!=frozen['adapter_sha256'] or sha(Path(__file__).with_name('native_transport.py'))!=frozen['transport_sha256']:raise ValueError('Frozen adapter changed')
    up=next((root/'upstream').glob('ethz-spylab-agentdojo-*'))
    for rel,digest in frozen['source_files'].items():
        if sha(up/rel)!=digest:raise ValueError('Frozen source changed: '+rel)
    dest=root/'runs'/frozen['phase_id']/job_id;dest.mkdir(parents=True,exist_ok=True)
    with (dest/'job.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        manifest={'plan_sha256':sha(plan_path),**case}
        if (dest/'manifest.json').exists() and json.loads((dest/'manifest.json').read_text())!=manifest:raise ValueError('Job changed')
        write_json(dest/'manifest.json',manifest)
        if (dest/'result.json').exists():return
        provider=RecordedNativeProvider(json.loads((root/'configs/provider-v02.json').read_text()),dest/'provider',job_id,limits=frozen['limits_per_job'])
        def create(**kwargs):
            body={k:v for k,v in kwargs.items() if not isinstance(v,openai.NotGiven)}
            body.update(max_tokens=frozen['max_output_tokens'],seed=case['seed'])
            response=provider.invoke('chat',body)
            print(json.dumps({'call':provider.sequence,'usage':response.get('usage',{})}),flush=True)
            return ChatCompletion.model_validate(response)
        client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        # Remove tenacity's retry wrapper only. The original request conversion runs.
        openai_llm.chat_completion_request=getattr(openai_llm.chat_completion_request,'__wrapped__',openai_llm.chat_completion_request)
        llm=openai_llm.OpenAILLM(client,MODEL,temperature=frozen['temperature']);llm.name=MODEL
        pipeline=AgentPipeline.from_config(PipelineConfig(llm=llm,model_id=MODEL,defense=case['defense'],system_message_name=None,system_message=None))
        suite=get_suite('v1.2.2',case['suite'])
        user=(suite.get_injection_task_by_id(case['user_task']) if case['kind']=='attacker_goal_feasibility' else suite.get_user_task_by_id(case['user_task']))
        attack=suite.get_injection_task_by_id(case['injection_task']) if case['injection_task'] else None
        write_json(dest/'status.json',{'status':'running','started':time.time()})
        try:
            with OutputLogger(str(dest/'native-traces')) as logger:
                with TraceLogger(delegate=logger,suite_name=case['suite'],user_task_id=case['user_task'],
                        injection_task_id=case['injection_task'],injections=case['injections'],attack_type='direct' if attack else 'none',
                        pipeline_name=pipeline.name,benchmark_version='v1.2.2') as trace:
                    utility,security=suite.run_task_with_pipeline(pipeline,user,attack,case['injections'])
                trace.set_contextarg('utility',utility);trace.set_contextarg('security',security)
            messages=json.loads(json.dumps(trace.messages,default=encode))
            last=messages[-1] if messages else {}
            exhausted=last.get('role')!='assistant' or bool(last.get('tool_calls'))
            write_json(dest/'result.json',{'status':'completed_with_execution_error' if exhausted else 'completed',
                'scope':frozen['scope'],**case,'native_utility':utility,'native_attack_success':security if attack else None,
                'utility':None if exhausted else utility,'attack_success':None if exhausted or attack is None else security,
                'execution_error':'native_tool_iteration_limit' if exhausted else None,'usage':provider.summary(),
                'messages':messages,'native_trace_error':trace.error})
            write_json(dest/'status.json',{'status':'completed','finished':time.time(),'execution_error':exhausted})
        except Exception as exc:
            write_json(dest/'status.json',{'status':'failed','error_type':type(exc).__name__,'error':str(exc),'usage':provider.summary(),'finished':time.time()});raise


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['plan','job'])
    p.add_argument('--root',default='.');p.add_argument('--plan',required=True);p.add_argument('--phase',default='formal-dojo-v04');p.add_argument('--job-id')
    a=p.parse_args();root=Path(a.root).resolve();path=Path(a.plan).resolve()
    if a.action=='plan':
        document=plan(root,a.phase)
        if path.exists() and json.loads(path.read_text())!=json.loads(json.dumps(document)):raise ValueError('Existing plan differs')
        write_json(path,document);print(json.dumps({'job_count':document['job_count'],'counts':document['counts'],'first_job':document['jobs'][0]['job_id']}))
    else:run_job(root,path,a.job_id)


if __name__=='__main__':main()
