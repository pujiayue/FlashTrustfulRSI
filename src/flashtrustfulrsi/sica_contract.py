"""Real Docker/native SICA tool-loop contract, scripted model responses only."""
import argparse,base64,hashlib,json,tempfile
from pathlib import Path
from flashtrustfulrsi.sica_container import seed_sica,run_sica
from flashtrustfulrsi.sandbox import DockerSandbox
from flashtrustfulrsi.original_container import files_sha
from flashtrustfulrsi.provider import write_json
from flashtrustfulrsi.native_container import apply_export

class Fixture:
 def __init__(self,config,verify_only=False):self.calls=[];self.config=config;self.verify_only=verify_only
 def invoke(self,protocol,body,request_id=None):
  assert protocol=='chat' and body['model']=='gpt-5.6-sol'
  command="python -c \"from base_agent.src.config import OFFLINE_CONTRACT_MARKER; from pathlib import Path; assert OFFLINE_CONTRACT_MARKER == 42; Path('fixture-proof.txt').write_text(str(OFFLINE_CONTRACT_MARKER))\""
  steps=[('software_developer',{'programming_instructions':'Verify the candidate config marker.' if self.verify_only else 'Open and edit your own base_agent/src/config.py to add the marker, then execute the modified module.'})]
  if not self.verify_only:
   steps += [('open_files',{'file_paths':['/tmp/agent/base_agent/src/config.py']}),
    ('overwrite_file',{'filepath':'/tmp/agent/base_agent/src/config.py','full_unabridged_new_content':self.config+'\nOFFLINE_CONTRACT_MARKER = 42\n'})]
  steps += [('execute_command',{'intent':'verify the modified native module','directory_in_which_to_run_command':'/tmp/agent','command':command,'command_returns':True,'generous_expected_duration':5}),
    ('return_result',{'result':'Modified module executed successfully.'}),('complete',{'all_complete':True}),
    ('submit_answer',{'answer':'42'}),('complete',{'all_complete':True})]
  i=len(self.calls)
  if i>=len(steps):raise AssertionError('Unexpected extra model request')
  name,args=steps[i]
  assert name in {t['function']['name'] for t in body.get('tools',[])},(i,name)
  self.calls.append({'request_id':request_id,'body':body})
  return {'id':'fixture-'+str(i),'object':'chat.completion','created':0,'model':'gpt-5.6-sol','choices':[{'index':0,'message':{'role':'assistant','content':None,'tool_calls':[{'id':'call_'+str(i),'type':'function','function':{'name':name,'arguments':json.dumps(args)}}]},'finish_reason':'tool_calls'}],'usage':{'prompt_tokens':10,'completion_tokens':10,'total_tokens':20}}

def main():
 p=argparse.ArgumentParser();p.add_argument('--source',type=Path,required=True);p.add_argument('--image',required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
 a.output.mkdir(parents=True,exist_ok=True)
 probe=DockerSandbox(image=a.image).probe();write_json(a.output/'sandbox-probe.json',probe)
 if not probe['limits_verified']:raise RuntimeError('Isolation not verified')
 with tempfile.TemporaryDirectory(prefix='ftrsi-sica-contract-') as tmp:
  seed=Path(tmp)/'agent';seed_sica(a.source,seed);before=files_sha(seed);config=(seed/'base_agent/src/config.py').read_text();fixture=Fixture(config)
  args=dict(agent_dir=seed,prompt='Offline integration fixture: create and execute the requested module in your own code tree. Submit 42.',image=a.image,provider=fixture,artifact_dir=a.output/'invocation',call_id='sica-offline-contract',timeout=90)
  try:result=run_sica(**args)
  finally:write_json(a.output/'mock-requests.json',fixture.calls)
  assert result['answer']=='42' and len(fixture.calls)==8
  assert base64.b64decode(result['files']['base_agent/src/config.py']['data']).decode()==config+'\nOFFLINE_CONTRACT_MARKER = 42\n'
  assert base64.b64decode(result['files']['fixture-proof.txt']['data'])==b'42'
  assert files_sha(seed)==before,'Input source mutated'
  assert run_sica(**args)==result and len(fixture.calls)==8,'Cache replay invoked model'
  candidate=Path(tmp)/'candidate';candidate.mkdir();apply_export(candidate,result['files']);(candidate/'fixture-proof.txt').unlink()
  verification=Fixture(config,verify_only=True)
  second=run_sica(agent_dir=candidate,prompt='Verify the previously modified config marker in a fresh container, then submit 42.',image=a.image,provider=verification,artifact_dir=a.output/'candidate-verification',call_id='sica-offline-verify',timeout=90)
  write_json(a.output/'candidate-mock-requests.json',verification.calls)
  assert second['answer']=='42' and len(verification.calls)==6
  assert base64.b64decode(second['files']['fixture-proof.txt']['data'])==b'42'
  write_json(a.output/'contract-result.json',{'status':'passed','image':a.image,'original_Agent_exec':True,'native_delegation':True,'native_overwrite_file':True,'native_execute_command':True,'generated_module_executed':True,'immutable_input':True,'cache_replay_without_calls':True,'real_tokenizer':True,'fixture_calls':14,'candidate_export_and_fresh_container_reexecution':True,'native_config_self_edit':True,'real_api_calls':0,'scope':'offline deterministic integration contract; NOT model capability or full evolution experiment'})
 print((a.output/'contract-result.json').read_text())
if __name__=='__main__':main()
