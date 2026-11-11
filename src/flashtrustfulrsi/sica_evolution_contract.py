"""Two-generation native SICA meta/archive contract; no paid model access."""
import argparse,json,re,shutil,tempfile,time
from pathlib import Path
from .sica_evolution import evolve
from .sandbox import DockerSandbox
from .provider import write_json

class MetaFixture:
 def __init__(self,candidate,value):
  self.config=(candidate/'base_agent/src/config.py').read_text();self.value=value;self.changelog=(candidate/'base_agent/agent_change_log.md').read_text() if (candidate/'base_agent/agent_change_log.md').exists() else 'Initial fixture.\n';self.calls=[];self.started=False;self.stage=0;self.actions=0;self.leaf=0;self.finished=False
 def invoke(self,protocol,body,request_id=None):
  assert protocol=='chat' and body['model']=='gpt-5.6-sol'
  names={t['function']['name'] for t in body['tools']}
  if 'meta_improvement_reasoning_structure' in names:
   if not self.started:name,args='meta_improvement_reasoning_structure',{};self.started=True
   elif self.stage<7:
    count=2 if self.stage==2 else 1
    if self.actions<count:
     self.actions+=1;self.leaf=0
     if self.stage<2:name,args='archive_explorer',{'analysis_focus':'Identify negative features' if self.stage==0 else 'Identify positive features'}
     elif self.stage==2:name,args='meta_improvement_review_committee',{'improvement_goals_and_context':'Offline wiring contract only; no capability claim.','coding_agent_improvement_proposal':'Update fixture marker, test module, document pending intervention.'}
     else:name,args='software_developer',{'programming_instructions':'Execute offline contract operation '+str(self.stage)}
    else:
     current=[n for n in names if n.startswith('step_') and n.endswith('_complete')];assert len(current)==1
     name,args=current[0],{'success':True};self.stage+=1;self.actions=0
   elif not self.finished:name,args='submit_answer',{'answer':'description.txt AND agent_change_log.md updated'};self.finished=True
   else:name,args='complete',{'all_complete':True}
  elif 'worst_problems' in names:
   choices=[('worst_problems',{'agent_iteration':0,'count':1,'benchmark_name':'adapter'}),('return_result',{'result':'Read native archived benchmark evidence; this is an offline integration fixture.'}),('complete',{'all_complete':True})]
   name,args=choices[self.leaf];self.leaf+=1
  elif 'overwrite_file' in names:
   if self.stage==3:
    text=re.sub(r'\nOFFLINE_CONTRACT_MARKER = \d+\n','\n',self.config)+f'\nOFFLINE_CONTRACT_MARKER = {self.value}\n';path='/home/agent/workdir/src/config.py'
    choices=[('open_files',{'file_paths':[path]}),('overwrite_file',{'filepath':path,'full_unabridged_new_content':text})]
   elif self.stage==4:
    command=f"python -c \"from workdir.src.config import OFFLINE_CONTRACT_MARKER; assert OFFLINE_CONTRACT_MARKER == {self.value}\""
    choices=[('execute_command',{'intent':'verify edited module imports','directory_in_which_to_run_command':'/home/agent','command':command,'command_returns':True,'generous_expected_duration':5})]
   else:
    path='/home/agent/workdir/'+('description.txt' if self.stage==5 else 'agent_change_log.md')
    text='Offline fixture intervention; effectiveness pending.\n' if self.stage==5 else self.changelog+'\nOffline intervention '+str(self.value)+'; effectiveness pending.\n'
    choices=[('open_files',{'file_paths':[path]}),('overwrite_file',{'filepath':path,'full_unabridged_new_content':text})]
   choices += [('return_result',{'result':'Offline contract operation completed.'}),('complete',{'all_complete':True})]
   name,args=choices[self.leaf];self.leaf+=1
  else:
   # Each native committee member has an independent history and read-only tools.
   prior=[t['function']['name'] for m in body['messages'] for t in (m.get('tool_calls') or [])]
   name,args=('complete',{'all_complete':True}) if 'return_result' in prior else ('return_result',{'result':'Fixture review: interface plan approved; not a model capability assessment.'})
  assert name in names,(name,names)
  i=len(self.calls);response={'id':f'fixture-{i}','object':'chat.completion','created':0,'model':'gpt-5.6-sol','choices':[{'index':0,'message':{'role':'assistant','content':None,'tool_calls':[{'id':f'call-{i}','type':'function','function':{'name':name,'arguments':json.dumps(args)}}]},'finish_reason':'tool_calls'}],'usage':{'prompt_tokens':1,'completion_tokens':1,'total_tokens':2}}
  self.calls.append({'request_id':request_id,'body':body,'response':response});return response

def main():
 p=argparse.ArgumentParser();p.add_argument('--source',type=Path,required=True);p.add_argument('--image',required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();a.output=a.output.resolve()
 providers=[]
 def factory(generation,candidate,archive):
  f=MetaFixture(candidate,42 if generation==1 else 0);providers.append(f);return f
 def evaluate(candidate,generation,dest):
  dest.mkdir(parents=True)
  script="import json; from base_agent.src import config; x=getattr(config,'OFFLINE_CONTRACT_MARKER',0); print(json.dumps([int(x==42),int(x*2==84)]))"
  with tempfile.TemporaryDirectory(prefix='ftrsi-sica-score-') as temp:
   workspace = Path(temp) / 'candidate'
   shutil.copytree(candidate, workspace)
   for path in [workspace, *workspace.rglob('*')]:
    path.chmod(0o755 if path.is_dir() else 0o644)
   proc = DockerSandbox(image=a.image).run_process(
    ['python', '-B', '-c', "import sys; sys.path.insert(0,'/workspace'); "+script],
    name='ftrsi-sica-contract-score', workdir=workspace, timeout=30)
  write_json(dest/'process.json',{'returncode':proc.returncode,'stdout':proc.stdout,'stderr':proc.stderr,'scope':'two correlated fixture checks, not benchmark examples or statistical evidence'})
  if proc.returncode:raise RuntimeError('Fixture scorer import failed')
  scores=json.loads(proc.stdout)
  return [{'problem_id':f'fixture-{i}','score':s,'tokens_used':0,'num_cached_tokens':0,'cost_estimate':0,'wall_time':0,'timed_out':False} for i,s in enumerate(scores)]
 try:
  result=evolve(source=a.source,output=a.output,image=a.image,provider_factory=factory,evaluate=evaluate,generations=2,timeout=180,protocol={'kind':'offline_deterministic_contract','real_api_calls':0,'benchmarks':'two correlated fixture assertions, not scientific samples','model':'gpt-5.6-sol'})
 finally:
  for i,provider in enumerate(providers):write_json(a.output/f'mock-model-{i+1}.json',provider.calls)
 assert [r['parent'] for r in result['lineage']]==[0,1]
 assert [r['mean_score'] for r in result['lineage']]==[1,0] and result['selected']==1
 assert all(p.stage==7 and p.finished for p in providers)
 write_json(a.output/'contract-result.json',{'status':'passed','generations':2,'parent_sequence':[0,1],'selected':1,'regressed_candidate_excluded':True,'seven_native_meta_steps_each_generation':True,'native_committee_and_archive_tools':True,'fixture_calls':sum(len(p.calls) for p in providers),'real_api_calls':0,'original_runner_main':False,'scope':'original meta/selector components in adapted Docker/evaluator driver; offline fixture, not paper reproduction'})
 print((a.output/'contract-result.json').read_text())
if __name__=='__main__':main()
