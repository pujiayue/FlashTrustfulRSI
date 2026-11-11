"""Original DGM outer search and coding agent with isolated YAML evaluation.

The published outer main/parent selection/archive functions execute unchanged.
The self_improve boundary is an adapter: native diagnosis prompts, original DGM
tool agent, and isolated benchmark scoring replace the upstream Docker builder.
"""
import argparse,ast,base64,contextlib,datetime,fcntl,hashlib,json,logging,os,random,shutil,tempfile,time
from pathlib import Path
from types import SimpleNamespace
from .original_container import run_original,seed_dgm,files_sha
from .native_container import apply_export
from .native_spool import NativeSpoolProvider
from .sandbox import DockerSandbox
from .provider import write_json
from .deepseek_debug import DebugBudget, DeepSeekProvider, MODEL as DEBUG_MODEL
from .dgm_deepseek_wire import DgmFlashTransport
from .yaml_twins import load_native,cases,evaluate_code,summarize


def native_namespace(path,provided):
    tree=ast.parse(path.read_text());tree.body=[n for n in tree.body
        if not isinstance(n,ast.ImportFrom) and not (isinstance(n,ast.If) and '__name__' in ast.unparse(n.test))]
    namespace={'__name__':'ftrsi_external_original',**provided}
    exec(compile(tree,str(path),'exec'),namespace);return namespace


def load_policies(source):
    provided={'load_json_file':lambda path:json.loads(Path(path).read_text()),'read_file':lambda path:Path(path).read_text()}
    prompts=native_namespace(source/'prompts/self_improvement_prompt.py',provided)
    evolution=native_namespace(source/'utils/evo_utils.py',provided)
    outer=native_namespace(source/'DGM_outer.py',{**provided,
        'find_selfimprove_eval_logs':prompts['find_selfimprove_eval_logs'],
        'load_dgm_metadata':evolution['load_dgm_metadata'],
        'is_compiled_self_improve':evolution['is_compiled_self_improve'],
        'ThreadPoolExecutor':__import__('concurrent.futures',fromlist=['ThreadPoolExecutor']).ThreadPoolExecutor,
        'as_completed':__import__('concurrent.futures',fromlist=['as_completed']).as_completed,
        'TimeoutError':TimeoutError})
    return prompts,outer


def execute(args):
    root=Path(args.root).resolve();source=root/'upstream/dgm-sol';dest=root/'runs'/args.job_id
    native=load_native(root);tasksets={arm:cases(native,arm) for arm in ('clean','poisoned','heldout')}
    debug = getattr(args, 'deepseek_debug', False)
    if debug:
        for arm, ids in {'clean': ('load_config','parse_settings'), 'poisoned': ('load_config','parse_settings'),
                         'heldout': ('parse_yaml','parse_upload')}.items():
            by_id = {row['id']: row for row in tasksets[arm]}
            tasksets[arm] = [by_id[key] for key in ids]
    spec={'job_id':args.job_id,'model':'gpt-5.6-sol','algorithm':'original_DGM',
        'scope':'native_DGM_outer_and_coding_agent_YAML_twins_integration_not_formal_T4',
        'upstream_commit':'a565fd2d1dca504ef5104a7cc0f3bdc4ab9b4fd2','source_files':files_sha(source),
        'image':args.image,'seed':args.seed,'generations':args.generations,'children_per_generation':args.children,
        'parent_selection':'score_child_prop','archive_rule':'keep_all','workers':1,'tasksets':tasksets,
        'final_selection':'maximum training utility in native archive; insertion-order tie; heldout only after search',
        'adapter_files':{name:hashlib.sha256((Path(__file__) if name=='dgm_native.py'
                                             else Path(__file__).with_name(name)).read_bytes()).hexdigest() for name in
            ('dgm_native.py','original_container.py','original_entry.py','native_container.py','native_entry.py','native_spool.py','yaml_twins.py')},
        'modifications':['gpt-5.6-sol for diagnosis, coding and self-improvement','published Trusting Trust patch plus versioned model/protocol patch',
            'original DGM main/parent/compile/archive functions; isolated self_improve execution adapter',
            'original YAML five-task train per arm and twelve-task heldout; no SWE/Polyglot aggregate claim',
            'fresh answer-free git history per solve; hidden tests and reference answers withheld until scoring',
            'native diagnosis receives training traces and tests, never heldout/witness outcomes',
            'offline fixed dependency image, resource limits; no arbitrary package installation',
            'native clock replaced solely for stable run-directory identity; single worker for deterministic replay']}
    if debug:
        budget = DebugBudget(requests=240,input_tokens=18_000_000,output_tokens=983_040,per_call_output=4096)
        spec.update(model=DEBUG_MODEL,scope='development_only_DGM_YAML_twins',runs_formal_eval=False,
                    provider_budget=budget.__dict__)
        spec['adapter_files']['dgm_deepseek_wire.py'] = hashlib.sha256(
            Path(__file__).with_name('dgm_deepseek_wire.py').read_bytes()).hexdigest()
        spec['modifications'][0] = ('official DeepSeek Flash for all model roles; bounded Chat adapter for native Responses '
                                   'tools; encrypted reasoning removed and vendor strict mode omitted; '
                                   'if a model turn emits parallel tools, expose only the first for native sequential dispatch')
        spec['modifications'][3] = 'two frozen native training cases per arm and two disjoint heldout cases'
    if args.plan_only:print(json.dumps(spec,indent=2));return
    dest.mkdir(parents=True,exist_ok=not debug,mode=0o700 if debug else 0o777)
    with (dest/'job.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if (dest/'manifest.json').exists() and json.loads((dest/'manifest.json').read_text())!=spec:raise ValueError('Frozen DGM manifest changed')
        write_json(dest/'manifest.json',spec)
        if (dest/'result.json').exists():return
        write_json(dest/'status.json',{'status':'running','phase':'initialization','started':time.time()})
        dev = DeepSeekProvider(dest/'provider',args.job_id,budget,timeout=180) if debug else None
        provider = DgmFlashTransport(dev) if debug else NativeSpoolProvider(root/'runs/model-spool',args.job_id,timeout=900)
        sandbox=DockerSandbox(image=args.image);prompts,outer=load_policies(source)
        initial=dest/'initial-agent'
        if not initial.exists():seed_dgm(source,initial)
        all_results={};original_cwd=Path.cwd();original_argv=__import__('sys').argv
        try:
            for arm in ('clean','poisoned'):
                armroot=dest/arm;armroot.mkdir(exist_ok=True);training=tasksets[arm];snapshots={'initial':initial};attempts=[]
                runroot=armroot/'output_dgm/native';runroot.mkdir(parents=True,exist_ok=True)
                def evaluate(asset,label,rows,output):
                    records=[];resolved=[];unresolved=[];empty=[]
                    preds=output/'predictions/native';preds.mkdir(parents=True,exist_ok=True)
                    for case in rows:
                        cid=case['id'];call=f'{arm}-{label}-{cid}'
                        with tempfile.TemporaryDirectory(prefix='ftrsi-answer-free-') as temp:
                            task=Path(temp);(task/(cid+'.py')).write_text(case['stub']);(task/'.docs').mkdir();(task/'.docs/instructions.md').write_text(case['instructions'])
                            document=run_original(agent_dir=asset,task_dir=task,instructions=case['instructions'],image=args.image,provider=provider,
                                artifact_dir=dest/'invocations'/call,call_id=call)
                        encoded=document['files'].get(cid+'.py')
                        if encoded is None:raise ValueError('Agent deleted required solution module')
                        code=base64.b64decode(encoded['data']).decode()
                        evaluation=evaluate_code(root,output/'cases'/cid,case,code,sandbox,native)
                        records.append({'case':case,'evaluation':evaluation,'code':code})
                        (preds/(cid+'.md')).write_text(document['native_log'])
                        (preds/(cid+'_eval.md')).write_text(evaluation['unit_stdout']+'\n'+evaluation['unit_stderr'])
                        write_json(preds/(cid+'.json'),{'model_patch':document['patch'],'eval_result':'pass' if evaluation['utility'] else 'fail'})
                        (resolved if evaluation['utility'] else empty if not document['patch'].strip() else unresolved).append(cid)
                    write_json(output/'evaluation.json',records)
                    performance={'accuracy_score':len(resolved)/len(rows),'total_submitted_instances':len(rows),
                        'total_resolved_instances':len(resolved),'total_resolved_ids':resolved,'total_unresolved_ids':unresolved,'total_emptypatch_ids':empty}
                    return performance,records
                performance,_=evaluate(initial,'initial-train',training,runroot/'initial')
                initial_metadata={'run_id':'initial','overall_performance':performance}
                write_json(runroot/'initial/metadata.json',initial_metadata)
                # initialize_run copies this freshly measured seed; paper cached scores are excluded.
                cached=armroot/'initial_polyglot';cached.mkdir(exist_ok=True)
                shutil.copytree(runroot/'initial',cached,dirs_exist_ok=True)
                # Upstream checks this name even though it copies into initial/.
                # Both names contain the same newly measured initial evidence.
                shutil.copytree(cached,runroot/'initial_polyglot',dirs_exist_ok=True)
                subset=armroot/'polyglot/subsets';subset.mkdir(parents=True,exist_ok=True)
                for size in ('small','medium'):write_json(subset/(size+'.json'),[c['id'] for c in training])
                dataset=[{'instance_id':c['id'],'language':'python','problem_statement':c['instructions'],
                    'reference_answers':'Reference implementation withheld; use observed test feedback.','reference_tests':c['test']} for c in training]
                def improve(**kwargs):
                    index=len(attempts);rid=f'candidate-{index:04d}';record={'run_id':rid,'parent_commit':kwargs['parent_commit']};attempts.append(record)
                    parent=snapshots[record['parent_commit']];out=runroot/rid;out.mkdir(exist_ok=True)
                    try:
                        with tempfile.TemporaryDirectory(prefix='ftrsi-dgm-diagnosis-') as temp:
                            view=Path(temp)/'code';shutil.copytree(parent,view);shutil.copyfile(view/'coding_agent.py',view/'coding_agent_polyglot.py')
                            system,prompt=prompts['get_diagnose_prompt_polyglot'](kwargs['entry'],record['parent_commit'],str(view),str(runroot),dataset)
                        response=provider.invoke('chat',{'model':'gpt-5.6-sol','messages':[{'role':'system','content':system},{'role':'user','content':prompt}],
                            'max_tokens':4096,'temperature':.7},request_id=f'{arm}-{rid}-diagnosis')
                        text=response['choices'][0]['message']['content'];match=__import__('re').search(r'\{[\s\S]*\}',text)
                        if not match:raise ValueError('Native diagnosis missing JSON')
                        diagnosis=json.loads(match[0]);instruction=prompts['get_problem_description_prompt'](diagnosis,True)
                        write_json(out/'diagnosis.json',{'system':system,'prompt':prompt,'response':response,'instruction':instruction})
                        document=run_original(agent_dir=parent,task_dir=parent,instructions=instruction,image=args.image,provider=provider,
                            artifact_dir=dest/'invocations'/f'{arm}-{rid}-edit',call_id=f'{arm}-{rid}-edit',self_improve=True)
                        candidate=out/'agent';candidate.mkdir(exist_ok=True);apply_export(candidate,document['files'])
                        (out/'model_patch.diff').write_text(document['patch']);record.update(model_patch_exists=True,model_patch_notempty=bool(document['patch'].strip()))
                        performance,_=evaluate(candidate,rid+'-train',training,out)
                        record['overall_performance']=performance;snapshots[rid]=candidate
                        write_json(out/'metadata.json',record);return record
                    except Exception as exc:
                        record.update(error_type=type(exc).__name__,error=str(exc));write_json(out/'metadata.json',record);raise
                outer['self_improve']=improve
                def logger(path):
                    log=logging.getLogger('ftrsi-dgm-'+arm);log.setLevel(logging.INFO)
                    handler=logging.FileHandler(path);log.addHandler(handler);return log
                outer['setup_logger']=logger
                outer['datetime']=SimpleNamespace(datetime=SimpleNamespace(now=lambda:SimpleNamespace(strftime=lambda fmt:'native')))
                os.chdir(armroot);random.seed(args.seed)
                __import__('sys').argv=['DGM_outer.py','--max_generation',str(args.generations),'--selfimprove_size',str(args.children),
                    '--selfimprove_workers','1','--num_swe_evals','1','--polyglot','--shallow_eval','--no_full_eval']
                write_json(dest/'status.json',{'status':'running','phase':arm+'-native-search'})
                outer['main']()
                if any('error' in attempt for attempt in attempts):raise RuntimeError('Native search recorded failed self-improvement; inspect metadata')
                state=outer['load_dgm_metadata'](str(runroot/'dgm_metadata.jsonl'),last_only=True)
                if len(attempts)!=args.generations*args.children:raise ValueError('Native search attempt denominator mismatch')
                archive=state['archive']
                best=max(archive,key=lambda rid:json.loads((runroot/rid/'metadata.json').read_text())['overall_performance']['accuracy_score'])
                _,before=evaluate(initial,'initial-heldout',tasksets['heldout'],armroot/'initial-heldout')
                _,after=evaluate(snapshots[best],'selected-heldout',tasksets['heldout'],armroot/'selected-heldout')
                all_results[arm]={'archive':archive,'selected':best,'attempts':attempts,'initial_heldout':summarize(before),'selected_heldout':summarize(after),
                    'selected_asset_hashes':files_sha(snapshots[best])}
            result={'status':'completed','scope':spec['scope'],'results':all_results,
                    'original_outer_main_executed':True,'original_coding_agent_executed':True,
                    'upstream_self_improve_function_unmodified':False,'formal_T4':False}
            if debug:
                result.update(model=DEBUG_MODEL,runs_formal_eval=False,ledger=dev.summary())
            write_json(dest/'result.json',result)
            write_json(dest/'status.json',{'status':'completed','finished':time.time()})
        except Exception as exc:
            write_json(dest/'status.json',{'status':'failed','error_type':type(exc).__name__,'error':str(exc),'finished':time.time()});raise
        finally:os.chdir(original_cwd);__import__('sys').argv=original_argv


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',default='.');p.add_argument('--job-id',required=True);p.add_argument('--image',required=True)
    p.add_argument('--seed',type=int,default=42);p.add_argument('--generations',type=int,default=1);p.add_argument('--children',type=int,default=1)
    p.add_argument('--deepseek-debug',action='store_true');p.add_argument('--plan-only',action='store_true')
    args=p.parse_args()
    if not __import__('re').fullmatch('[a-zA-Z0-9_-]{1,80}',args.job_id):raise ValueError('Invalid job')
    if not 1<=args.generations<=80 or not 1<=args.children<=4:raise ValueError('Invalid search size')
    if args.deepseek_debug and (not args.job_id.startswith('dev-dsflash-dgm-') or args.generations != 2 or args.children != 1):
        p.error('DeepSeek debug requires a separate dev-dsflash-dgm ID, two generations and one child per generation')
    execute(args)


if __name__=='__main__':main()
