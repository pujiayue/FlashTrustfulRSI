"""Native SICA meta-improvement + archive selection, isolated execution adapter.

Original selector/Analyzer/statistics and improve_agent are executed. The host
Docker/benchmark scheduling is adapted; evaluators and providers are injected.
"""
import ast,base64,fcntl,hashlib,importlib.util,json,logging,math,shutil,sys,time
from pathlib import Path
from .sica_container import seed_sica
from .sica_meta_container import run_meta
from .native_container import apply_export
from .original_container import files_sha
from .provider import write_json

def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()

CHECKPOINT_TREES = ('archive', 'candidates/1', 'evaluations/0', 'evaluations/1', 'meta/1')
CHECKPOINT_FILES = ('manifest.json', 'lineage.json', 'meta/2/input.json', 'status.json', '../status.json')


def checkpoint_inventory(previous):
    """Fingerprint the committed first generation and frozen next input."""
    previous = Path(previous)
    return {
        'trees': {name: hashlib.sha256(json.dumps(files_sha(previous/name), sort_keys=True).encode()).hexdigest()
                  for name in CHECKPOINT_TREES},
        'files': {name: sha(previous/name) for name in CHECKPOINT_FILES},
    }


def validate_checkpoint(previous, review, plan):
    if plan['generations'] != 2:
        raise ValueError('Committed recovery requires a two-generation SICA plan')
    if checkpoint_inventory(previous) != review['inventory']:
        raise ValueError('SICA committed checkpoint changed after review')
    old = json.loads((previous/'manifest.json').read_text())
    for key in ('source_files', 'image', 'generations', 'timeout', 'selection'):
        if old[key] != plan[key]:
            raise ValueError('SICA checkpoint changed '+key)
    bookkeeping = {'adapter_sha256', 'max_requests', 'dev_reuse', 'committed_resume'}
    if ({key: value for key, value in old['protocol'].items() if key not in bookkeeping}
            != {key: value for key, value in plan['protocol'].items() if key not in bookkeeping}):
        raise ValueError('SICA checkpoint protocol differs from the new run')
    if (json.loads((previous/'../status.json').read_text())['status'] != 'failed'
            or json.loads((previous/'status.json').read_text())['status'] != 'failed'
            or (previous/'meta/2/result.json').exists()):
        raise ValueError('Expected an interrupted second SICA generation')
    lineage = json.loads((previous/'lineage.json').read_text())
    if [row['generation'] for row in lineage] != [1]:
        raise ValueError('Expected exactly one committed SICA generation')
    frozen = json.loads((previous/'meta/2/input.json').read_text())
    if hashlib.sha256(json.dumps(frozen, sort_keys=True).encode()).hexdigest() != review['next_input_sha256']:
        raise ValueError('SICA next input differs from the independently verified checkpoint')
    if any(sha(Path(__file__).with_name(name)) != digest for name, digest in frozen['adapter_files'].items()):
        raise ValueError('SICA frozen invocation adapter changed')
    return lineage, frozen


class NativeSelector:
 def __init__(self,source):
  self.runner=Path(source)/'runner.py';self.analysis=Path(source)/'base_agent/src/utils/archive_analysis.py'
  self.hashes={str(p):sha(p) for p in (self.runner,self.analysis)}
  name='_ftrsi_sica_archive_'+sha(self.analysis)[:12]
  spec=importlib.util.spec_from_file_location(name,self.analysis);mod=importlib.util.module_from_spec(spec);sys.modules[name]=mod;spec.loader.exec_module(mod)
  nodes=[n for n in ast.parse(self.runner.read_text()).body if isinstance(n,ast.FunctionDef) and n.name=='select_base_agent']
  if len(nodes)!=1:raise ValueError('Original selector missing')
  ns={'ArchiveAnalyzer':mod.ArchiveAnalyzer,'ScoreType':mod.ScoreType,'compute_statistics':mod.compute_statistics,'logger':logging.getLogger(__name__)}
  exec(compile(ast.Module(body=nodes,type_ignores=[]),str(self.runner),'exec'),ns)
  self.module=mod;self.function=ns['select_base_agent']
 def select(self,archive,current):
  for p,h in self.hashes.items():
   if sha(p)!=h:raise ValueError('Frozen selector source changed')
  analyzer=self.module.ArchiveAnalyzer(archive)
  scores,summaries=analyzer.get_problem_scores_by_iteration()
  if scores.empty or summaries.empty:raise ValueError('Empty archive cannot silently select seed')
  stats=self.module.compute_statistics(scores,summaries,score_type='mean_score')
  if not all(math.isfinite(float(v)) for v in stats['ci_lower']):raise ValueError('Undefined native confidence bounds; require >=2 scored cases')
  chosen=int(self.function(analyzer,current,score_type='mean_score'))
  if not (Path(archive)/f'agent_{chosen}'/'agent_code').is_dir():raise ValueError('Selected agent missing')
  return chosen,json.loads(stats.to_json(orient='records'))

def record_benchmark(destination,rows):
 if len(rows)<2 or len({r['problem_id'] for r in rows})!=len(rows):raise ValueError('At least two unique scored cases required')
 for r in rows:
  for k in ['score','tokens_used','num_cached_tokens','cost_estimate','wall_time']:
   if type(r[k]) not in (int,float) or not math.isfinite(r[k]):raise ValueError('Invalid metric '+k)
  if not 0<=r['score']<=1 or any(r[k]<0 for k in ['tokens_used','num_cached_tokens','cost_estimate','wall_time']):raise ValueError('Invalid benchmark range')
 b=destination/'benchmarks'/'adapter';b.mkdir(parents=True)
 (b/'results.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows))
 write_json(b/'perf.json',{'avg_score':sum(r['score'] for r in rows)/len(rows),'tokens':sum(r['tokens_used'] for r in rows),'num_cached_tokens':sum(r['num_cached_tokens'] for r in rows),'cost':sum(r['cost_estimate'] for r in rows),'time':sum(r['wall_time'] for r in rows)})

def evolution_plan(source, image, generations, timeout, protocol):
 return {'source_files':files_sha(source),'image':image,'generations':generations,'timeout':timeout,'protocol':protocol,
  'adapter_files':{n:sha(Path(__file__).with_name(n)) for n in ['sica_evolution.py','sica_meta_container.py','sica_meta_entry.py']},'selection':'native select_base_agent mean_score; latest mean >= best lower 95% bound'}

def evolve(*,source,output,image,provider_factory,evaluate,generations=2,timeout=1800,protocol,checkpoint=None):
 source=Path(source).resolve();output=Path(output).resolve();output.mkdir(parents=True,exist_ok=True)
 if not 1<=generations<=100:raise ValueError('Invalid generation count')
 selector=NativeSelector(source)
 plan=evolution_plan(source,image,generations,timeout,protocol)
 with (output/'job.lock').open('a') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  if (output/'manifest.json').exists():
   if json.loads((output/'manifest.json').read_text())!=plan:raise ValueError('Evolution inputs changed')
   if (output/'result.json').exists():return json.loads((output/'result.json').read_text())
   raise RuntimeError('Incomplete evolution requires explicit recovery; no blind rerun')
  if checkpoint:
   previous,review=checkpoint
   lineage,frozen=validate_checkpoint(previous,review,plan)
  else:
   lineage=[]
  write_json(output/'manifest.json',plan);archive=output/'archive'
  try:
   if checkpoint:
    for name in CHECKPOINT_TREES:
     shutil.copytree(previous/name,output/name)
     copied=hashlib.sha256(json.dumps(files_sha(output/name),sort_keys=True).encode()).hexdigest()
     if copied!=review['inventory']['trees'][name]:raise ValueError('Copied SICA checkpoint differs: '+name)
    write_json(output/'lineage.json',lineage)
    # Let the normal container entry enforce the full original input identity.
    write_json(output/'meta/2/input.json',frozen)
   else:
    archive.mkdir()
    seed=output/'seed';seed_sica(source,seed)
    initial=archive/'agent_0';initial.mkdir();shutil.copytree(seed/'base_agent',initial/'agent_code')
    record_benchmark(initial,evaluate(seed,0,output/'evaluations'/'0'))
   for generation in range(len(lineage)+1,generations+1):
    parent,stats=selector.select(archive,generation-1);archive_before=files_sha(archive)
    write_json(output/'status.json',{'status':'running','generation':generation,'parent':parent})
    candidate=output/'candidates'/str(generation);candidate.mkdir(parents=True)
    shutil.copytree(archive/f'agent_{parent}'/'agent_code',candidate/'base_agent')
    latest=archive/f'agent_{generation-1}'/'agent_code'/'agent_change_log.md'
    if latest.exists():shutil.copyfile(latest,candidate/'base_agent'/'agent_change_log.md')
    actor=selector.module.ArchiveAnalyzer(archive).get_best_agent_iteration()
    if checkpoint and (parent!=review['next_parent'] or actor!=frozen['request']['best_iter']
                       or archive_before!=frozen['archive_files'] or files_sha(candidate)!=frozen['agent_files']):
     raise ValueError('SICA second-generation parent, actor, archive or candidate changed')
    provider=provider_factory(generation,candidate,archive)
    document=run_meta(agent_dir=candidate,archive_dir=archive,best_iter=actor,current_iter=generation,image=image,provider=provider,artifact_dir=output/'meta'/str(generation),call_id=f'sica-meta-{generation}',timeout=timeout)
    events=json.loads(base64.b64decode(document['logs']['meta-events.json']['data']))
    passed=[e['name'] for e in events if e['result'] and e['result'].get('success')]
    if 'meta_improvement_reasoning_structure' not in passed or len({n for n in passed if n and n.startswith('step_') and n.endswith('_complete')})!=7:
     raise RuntimeError('Native seven-step meta reasoning did not complete')
    if files_sha(archive)!=archive_before:raise ValueError('Historical archive changed')
    apply_export(candidate,document['files'])
    staged=output/f'pending-agent-{generation}';staged.mkdir();shutil.copytree(candidate/'base_agent',staged/'agent_code')
    rows=evaluate(candidate,generation,output/'evaluations'/str(generation));record_benchmark(staged,rows)
    staged.rename(archive/f'agent_{generation}')
    lineage.append({'generation':generation,'parent':parent,'meta_actor':actor,'selection_statistics':stats,'candidate_hashes':files_sha(candidate),'mean_score':sum(r['score'] for r in rows)/len(rows),'archive_before_sha256':hashlib.sha256(json.dumps(archive_before,sort_keys=True).encode()).hexdigest()})
    write_json(output/'lineage.json',lineage)
   selected,stats=selector.select(archive,generations)
   result={'status':'completed','generations':generations,'selected':selected,'lineage':lineage,'final_selection_statistics':stats,'original_improve_agent':True,'original_meta_reasoning_tool':True,'original_archive_selector':True,'original_runner_main':False,'protocol':protocol,'scope':'adapted isolated execution and benchmark scheduling; model-effect claims depend on actual provider/evaluator evidence'}
   write_json(output/'result.json',result);write_json(output/'status.json',{'status':'completed','selected':selected});return result
  except Exception as exc:
   write_json(output/'status.json',{'status':'failed','error_type':type(exc).__name__,'error':str(exc),'finished':time.time()});raise
