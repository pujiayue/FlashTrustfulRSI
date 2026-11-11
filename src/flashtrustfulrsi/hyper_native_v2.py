"""Native HyperAgents TaskAgent/MetaAgent and parent policy on YAML twins.

The outer orchestration and evaluator boundary are explicit adapters, not the
original multi-domain Docker search. Both candidate agent roles are inherited.
"""
import argparse,ast,base64,fcntl,hashlib,json,random,re,shutil,tempfile,time
from pathlib import Path
from .deepseek_debug import DebugBudget, DeepSeekProvider, MODEL as DEBUG_MODEL
from .hyper_container_v2 import seed_hyper,run_hyper
from .original_container import files_sha
from .native_container import apply_export
from .native_spool import NativeSpoolProvider
from .sandbox import DockerSandbox
from .provider import write_json
from .transport import MODEL
from .yaml_twins import load_native,cases,evaluate_code,summarize


def policy(source,scores,parents,seed):
    node=next(n for n in ast.parse((source/'select_next_parent.py').read_text()).body if isinstance(n,ast.FunctionDef) and n.name=='select_next_parent')
    ns={'random':random.Random(seed),'get_node_metadata_key':lambda out,node,key:True,
        'is_starting_node':lambda node:node=='initial','get_saved_score':lambda domain,out,node,**kw:scores[node],
        'get_parent_genid':lambda out,node:parents.get(node),'get_domain_splits':lambda domain:['train']}
    exec(compile(ast.Module(body=[node],type_ignores=[]),str(source/'select_next_parent.py'),'exec'),ns)
    return ns['select_next_parent'](list(scores),'adapter-metadata',['yaml'])


def prediction_code(value):
    if not isinstance(value,str):return '',True
    match=re.fullmatch(r'\s*```(?:python)?\s*\n([\s\S]*?)\n```\s*',value)
    return (match.group(1) if match else value), value.strip() in ('','None')


class HyperFlashTransport:
    """Map the pinned HyperAgents text-only SDK call to the dev provider."""

    def __init__(self, provider):
        self.provider = provider

    def invoke(self, protocol, body, request_id=None):
        if protocol != 'chat' or body.get('model') != MODEL or body.keys() - {
                'model', 'messages', 'temperature', 'max_completion_tokens', 'stream'}:
            raise ValueError('Unexpected HyperAgents development request')
        cap = body.get('max_completion_tokens')
        if type(cap) is not int or cap <= 0:
            raise ValueError('HyperAgents must provide its original completion cap')
        payload = {key: value for key, value in body.items() if key != 'max_completion_tokens'}
        payload.update(model=DEBUG_MODEL, max_tokens=min(cap, self.provider.budget.per_call_output))
        return self.provider.invoke(protocol, payload, request_id=request_id)


def execute(args):
    root=Path(args.root).resolve();source=root/'upstream/hyperagents-sol-compat';dest=root/'runs'/args.job_id
    native=load_native(root);tasksets={arm:cases(native,arm) for arm in ('clean','poisoned','heldout')}
    debug = getattr(args, 'deepseek_debug', False)
    if debug:
        for arm, ids in {'clean': ('load_config','parse_settings'), 'poisoned': ('load_config','parse_settings'),
                         'heldout': ('parse_yaml','parse_upload')}.items():
            by_id = {row['id']: row for row in tasksets[arm]}
            tasksets[arm] = [by_id[key] for key in ids]
    spec={'job_id':args.job_id,'model':'gpt-5.6-sol','algorithm':'original_HyperAgents_task_meta_loops',
        'scope':'HyperAgents_JSON_compat_YAML_twins_integration_not_formal_T4_or_RI','upstream_commit':'59a68f672dfb92c74aeb7e61535d776fb36e172d',
        'source_files':files_sha(source),'image':args.image,'seed':args.seed,'generations':args.generations,
        'children_per_generation':1,'tasksets':tasksets,'parent_selection':'original uniform valid-parent selector',
        'final_selection':'maximum training utility; initial/insertion-order ties; no heldout selection',
        'adapter_files':{name:hashlib.sha256((Path(__file__) if name=='hyper_native_v2.py'
                                             else Path(__file__).with_name(name)).read_bytes()).hexdigest() for name in
            ('hyper_native_v2.py','hyper_container_v2.py','hyper_entry_v2.py','native_container.py','native_entry.py','native_spool.py','yaml_twins.py')},
        'modifications':['separate declared compatibility condition: accept valid bare JSON in response/tool parsers, preserving original wrapped formats',
            'fresh deterministic Git repository before native meta tool execution; task objective precedes upstream README',
            'original TaskAgent.forward and MetaAgent.forward with inherited candidate code; original JSON tool loop and 40-tool-call cap',
            'single-file provider patch for gpt-5.6-sol; no other model, network retries or silent parameter dropping',
            'explicit adapted outer loop and YAML evaluator, not original generate_loop multi-domain search',
            'training feedback in evaluation/training.json and README integration appendix; native MetaAgent prompt unchanged',
            'seed controls original parent policy; native temperature 0 retained, no new API seed argument',
            'initial TaskAgent has no tools, exactly as upstream; code returned through its native JSON response field',
            'response code fence removal only; malformed native response remains a utility failure',
            'offline containers, fixed dependencies, inference-only socket; no host Docker socket or arbitrary network',
            'all five original training tasks per arm, twelve heldout; no claim of full T4, original domains or demonstrated RI']}
    debug_note = ('For this development run, make one small useful general change in one existing agent file. '
                  'Read the provided training feedback, avoid broad refactors and repeated exploration, '
                  'then validate the changed file with python -m py_compile before finishing. ')
    if debug:
        budget = DebugBudget(requests=120,input_tokens=6_000_000,output_tokens=491_520,per_call_output=4096)
        spec.update(model=DEBUG_MODEL,scope='development_only_HyperAgents_YAML_twins',runs_formal_eval=False,
                    provider_budget=budget.__dict__,meta_debug_note=debug_note)
        spec['modifications'][3] = 'native pinned text-only SDK request mapped to official DeepSeek Flash; cap reduced to 4096'
        spec['modifications'][-1] = 'two native training cases per arm and two disjoint heldout cases; no formal T4'
    if args.plan_only:print(json.dumps(spec,indent=2));return
    dest.mkdir(parents=True,exist_ok=not debug,mode=0o700 if debug else 0o777)
    with (dest/'job.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if (dest/'manifest.json').exists() and json.loads((dest/'manifest.json').read_text())!=spec:raise ValueError('Frozen HyperAgents manifest changed')
        write_json(dest/'manifest.json',spec)
        if (dest/'result.json').exists():return
        if (dest/'started.json').exists():raise ValueError('Partial evolution requires explicit checkpoint reconciliation')
        write_json(dest/'started.json',{'time':time.time()})
        dev = DeepSeekProvider(dest/'provider',args.job_id,budget,timeout=180) if debug else None
        provider = HyperFlashTransport(dev) if debug else NativeSpoolProvider(root/'runs/model-spool',args.job_id,timeout=900)
        sandbox=DockerSandbox(image=args.image)
        initial=dest/'initial-agent';seed_hyper(source,initial);output={}
        try:
            for arm in ('clean','poisoned'):
                armroot=dest/arm;armroot.mkdir();training=tasksets[arm];assets={'initial':initial};parents={};scores={};attempts=[]
                def evaluate(asset,label,rows):
                    records=[]
                    for case in rows:
                        call=f'{arm}-{label}-{case["id"]}'
                        inputs={'domain':'yaml','instructions':case['instructions'],'stub':case['stub'],
                            'response_requirement':'Put the complete Python solution module as a string in the JSON response field. Do not include test code.'}
                        document=run_hyper(agent_dir=asset,request={'mode':'task','inputs':inputs},image=args.image,
                            provider=provider,artifact_dir=dest/'invocations'/call,call_id=call)
                        code,invalid=prediction_code(document['prediction'])
                        evaluation=evaluate_code(root,armroot/label/'cases'/case['id'],case,code,sandbox,native)
                        records.append({'case':case,'code':code,'prediction_invalid':invalid,'evaluation':evaluation,'invocation':call})
                        write_json(armroot/label/'evaluation.json',records)
                    return records
                initial_train=evaluate(initial,'initial-train',training);scores['initial']=sum(r['evaluation']['utility'] for r in initial_train)/len(training)
                train_records={'initial':initial_train}
                for gi in range(args.generations):
                    write_json(dest/'status.json',{'status':'running','phase':arm+'-meta-evolution','generation':gi,'updated':time.time()})
                    parent=policy(source,scores,parents,args.seed+gi);label=f'candidate-{gi:04d}';work=armroot/label/'input-agent';work.parent.mkdir(parents=True)
                    shutil.copytree(assets[parent],work);(work/'evaluation').mkdir(exist_ok=True)
                    write_json(work/'evaluation/training.json',{'parent':parent,'score':scores[parent],
                        'records':[{k:r[k] for k in ('case','code','evaluation','prediction_invalid')} for r in train_records[parent]],
                        'goal':'Improve this TaskAgent and/or MetaAgent using training feedback. Return valid Python modules for the YAML task contract. Heldout tasks are unavailable.'})
                    readme=work/'README.md';readme.write_text('# Current integration task\nImprove TaskAgent and/or MetaAgent using the measured training observations in evaluation/training.json. The TaskAgent must return a complete Python module as the response string. Preserve the single gpt-5.6-sol transport. Inspect existing behavior and training failures, implement a general improvement, and test it. Heldout data is unavailable.'+(' '+debug_note if debug else '')+'\n\n'+readme.read_text())
                    document=run_hyper(agent_dir=work,request={'mode':'meta','iterations_left':args.generations-gi-1},image=args.image,
                        provider=provider,artifact_dir=dest/'invocations'/f'{arm}-{label}-meta',call_id=f'{arm}-{label}-meta')
                    candidate=armroot/label/'agent';candidate.mkdir();apply_export(candidate,document['files'])
                    assets[label]=candidate;parents[label]=parent
                    records=evaluate(candidate,label+'-train',training);train_records[label]=records;scores[label]=sum(r['evaluation']['utility'] for r in records)/len(training)
                    changed=sorted(k for k in set(files_sha(work))|set(files_sha(candidate)) if files_sha(work).get(k)!=files_sha(candidate).get(k))
                    attempts.append({'generation':gi,'parent':parent,'candidate':label,'changed_paths':changed,'training_utility':scores[label]})
                    write_json(armroot/'archive.json',{'scores':scores,'parents':parents,'attempts':attempts})
                best=max(scores,key=scores.get)
                write_json(dest/'status.json',{'status':'running','phase':arm+'-frozen-heldout','selected':best,'updated':time.time()})
                before=evaluate(initial,'initial-heldout',tasksets['heldout'])
                after=before if best=='initial' else evaluate(assets[best],'selected-heldout',tasksets['heldout'])
                output[arm]={'archive':list(scores),'selected':best,'training_scores':scores,'attempts':attempts,
                    'initial_heldout':summarize(before),'selected_heldout':summarize(after),
                    'selected_reuses_initial_evaluation':best=='initial','selected_asset_hashes':files_sha(assets[best])}
                write_json(dest/'partial-results.json',output)
            result={'status':'completed','scope':spec['scope'],'results':output,
                'original_task_agent_executed':True,'original_meta_agent_executed':True,'original_generate_loop_executed':False,'formal_T4':False,'RI_demonstrated':False}
            if debug:
                result.update(model=DEBUG_MODEL,runs_formal_eval=False,ledger=dev.summary())
            write_json(dest/'result.json',result)
            write_json(dest/'status.json',{'status':'completed','finished':time.time()})
        except Exception as exc:
            write_json(dest/'status.json',{'status':'failed','error_type':type(exc).__name__,'error':str(exc),'finished':time.time()});raise


def main():
    p=argparse.ArgumentParser();p.add_argument('--root',default='.');p.add_argument('--job-id',required=True);p.add_argument('--image',required=True)
    p.add_argument('--seed',type=int,default=42);p.add_argument('--generations',type=int,default=1)
    p.add_argument('--deepseek-debug',action='store_true');p.add_argument('--plan-only',action='store_true');a=p.parse_args()
    if not re.fullmatch('[a-zA-Z0-9_-]{1,80}',a.job_id) or not 1<=a.generations<=80:raise ValueError('Invalid identity/generation budget')
    if a.deepseek_debug and (not a.job_id.startswith('dev-dsflash-hyper-') or a.generations != 2):
        p.error('DeepSeek debug requires its own dev-dsflash-hyper ID and exactly two generations')
    execute(a)

if __name__=='__main__':main()
