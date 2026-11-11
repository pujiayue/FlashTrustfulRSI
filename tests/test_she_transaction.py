import importlib.util,json,tempfile,unittest,os
from pathlib import Path
from flashtrustfulrsi.she_guarded_evolution import transactional_patch
from flashtrustfulrsi.native_transport import TransportError
from unittest.mock import patch

@unittest.skipUnless(importlib.util.find_spec('she'),'external SHE research dependency required')
class TransactionTests(unittest.TestCase):
 def test_full_job_rejects_invalid_proposal_then_runs_frozen_test(self):
  from flashtrustfulrsi.she_guarded_evolution import make_plan,run_job
  actual=Path(os.environ.get('FTRSI_NATIVE_ROOT','.')).resolve()
  if not list((actual/'upstream').glob('RainbowQTT-SHE-*')):self.skipTest('Pinned source required')
  with tempfile.TemporaryDirectory(prefix='ftrsi-she-transaction-contract-') as tmp:
   root=Path(tmp);(root/'upstream').symlink_to(actual/'upstream',target_is_directory=True);(root/'configs').mkdir();(root/'configs/provider-v02.json').write_text('{}')
   spec=make_plan(root,'fixture-only');spec['updates']=1;spec['scope']='offline_fixture_not_experiment';job=spec['jobs'][0]
   job['train']=job['train'][:1];job['test']=job['test'][:1];spec['jobs']=[job];spec['job_count']=1
   path=root/'plan.json';path.write_text(json.dumps(spec));calls=[]
   def rollout(provider,case,artifacts,seed,identity,dest):
    calls.append(identity)
    return {**case,'trajectory_id':identity,'rollout_id':0,'condition':'clean','fulfillable':True,'failed':False,'unsafe':False,
     'partial_unsafe':False,'full_unsafe':False,'attack_success':False,'task_completed':True,'tool_calls':[],'blocked_actions':[],
     'reasoning_trace':[],'rule_hits':[],'final_response':'fixture only'}
   class Provider:
    def __init__(self,*args,**kwargs):pass
    def invoke(self,*args,**kwargs):return {'choices':[{'message':{'content':'{"changes":[],"file_updates":[{"path":"artifacts/safety_memory.jsonl","mode":"replace_jsonl","content":""}]}'}}]}
    def summary(self):return {'fixture':True,'physical_requests':0}
   with patch('flashtrustfulrsi.she_guarded_evolution.DurableProvider',Provider),patch('flashtrustfulrsi.she_evolution.rollout',rollout):run_job(root,path,job['job_id'])
   result=json.loads((root/'runs/fixture-only'/job['job_id']/'result.json').read_text())
   self.assertEqual(result['rejected_proposals'],1);self.assertEqual(result['updates'],0);self.assertEqual(result['scheduled_proposals'],1)
   self.assertEqual(calls[-2:],['test-seed-c0000-r0','test-selected-c0000-r0'])
 def execute(self,root,kind):
  active=root/'active';active.mkdir();(active/'system_prompt.md').write_text('original');rd=root/'R00';rd.mkdir()
  def runner(script,argv):
   staged=Path(argv[argv.index('--artifacts-dir')+1]);(staged/'system_prompt.md').write_text('candidate')
   if kind=='transport':raise TransportError('fixture transport failure')
   (rd/'evolve_agent_raw.txt').write_text('fixture candidate')
   if kind=='invalid':raise ValueError('Unsupported native patch mode')
  transactional_patch('propose_artifact_patch.py',['--artifacts-dir',active,'--round-dir',rd],runner)
  return active,rd
 def test_invalid_patch_rolls_back_and_consumes_attempt(self):
  with tempfile.TemporaryDirectory() as tmp:
   active,rd=self.execute(Path(tmp),'invalid');self.assertEqual((active/'system_prompt.md').read_text(),'original')
   self.assertEqual(json.loads((rd/'transaction.json').read_text())['status'],'rejected')
   self.assertEqual(json.loads((rd/'change_manifest.json').read_text())['changes'],[])
 def test_valid_commit_and_transport_stays_fatal(self):
  with tempfile.TemporaryDirectory() as tmp:
   active,rd=self.execute(Path(tmp),'valid');self.assertEqual((active/'system_prompt.md').read_text(),'candidate')
   self.assertEqual((rd/'pre-proposal-artifacts/system_prompt.md').read_text(),'original')
  with tempfile.TemporaryDirectory() as tmp:
   with self.assertRaises(TransportError):self.execute(Path(tmp),'transport')
   self.assertEqual((Path(tmp)/'active/system_prompt.md').read_text(),'original')

if __name__=='__main__':unittest.main()
