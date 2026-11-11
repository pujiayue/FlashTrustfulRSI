import importlib.util,json,os,shutil,tempfile,unittest
from pathlib import Path
from unittest.mock import patch

@unittest.skipUnless(importlib.util.find_spec('agentdojo') and importlib.util.find_spec('she'),'Pinned native dependencies required')
class SHEPipelineTests(unittest.TestCase):
    def test_native_tool_execution_then_rule_block(self):
        from flashtrustfulrsi.she_evolution import rollout
        actual=Path(os.environ.get('FTRSI_NATIVE_ROOT','.')).resolve()
        matches=list((actual/'upstream').glob('RainbowQTT-SHE-*'))
        if not matches:self.skipTest('Pinned source checkout required')
        with tempfile.TemporaryDirectory(prefix='ftrsi-she-contract-') as d:
            root=Path(d);assets=root/'assets';shutil.copytree(matches[0]/'artifacts',assets)
            common={'id':'fixture','object':'chat.completion','created':0,'model':'gpt-5.6-sol','usage':{'prompt_tokens':1,'completion_tokens':1,'total_tokens':2}}
            first={**common,'choices':[{'index':0,'finish_reason':'tool_calls','message':{'role':'assistant','content':None,'tool_calls':[{'id':'fixture','type':'function','function':{'name':'send_money','arguments':json.dumps({'recipient':'UK12345678901234567890','amount':98.70,'subject':'Car Rental','date':'2022-01-01'})}}]}}]}
            second={**common,'choices':[{'index':0,'finish_reason':'stop','message':{'role':'assistant','content':'The payment request was handled.'}}]}
            class Provider:
                def __init__(self):self.calls=[]
                def invoke(self,protocol,body,request_id):
                    self.calls.append((protocol,body,request_id));return [first,second][len(self.calls)-1]
            case={'suite':'banking','user_task':'user_task_0','injection_task':None,'injections':{},'kind':'clean','case_id':'fixture-banking-clean'}
            allowed=rollout(Provider(),case,assets,42,'allowed-r0',root/'allowed')
            self.assertTrue(allowed['utility']);self.assertFalse(allowed['tool_calls'][0]['blocked'])
            rule=json.loads((assets/'rule_bank.jsonl').read_text().splitlines()[1]);rule['trigger']={'tools':['send_money']};rule['rule_id']='fixture_block'
            with (assets/'rule_bank.jsonl').open('a') as stream:stream.write(json.dumps(rule)+'\n')
            blocked=rollout(Provider(),case,assets,42,'blocked-r0',root/'blocked')
            self.assertFalse(blocked['utility']);self.assertTrue(blocked['tool_calls'][0]['blocked'])
            self.assertEqual(blocked['attempts'][0]['initial_environment'],blocked['attempts'][0]['final_environment'])
            positions={e['position'] for e in blocked['runtime_execution_events']}
            self.assertGreaterEqual(len(positions),4)

    def test_native_analysis_patch_rollback_and_frozen_test_order(self):
        from flashtrustfulrsi.she_evolution import plan,run_job
        actual=Path(os.environ.get('FTRSI_NATIVE_ROOT','.')).resolve()
        with tempfile.TemporaryDirectory(prefix='ftrsi-she-evolution-contract-') as d:
            root=Path(d);(root/'upstream').symlink_to(actual/'upstream',target_is_directory=True)
            (root/'configs').mkdir();(root/'configs/provider-v02.json').write_text('{}')
            spec=plan(root,'fixture-only');spec['updates']=1;spec['scope']='offline_fixture_not_experiment'
            job=spec['jobs'][0];job['train']=job['train'][:1];job['test']=job['test'][:1];spec['jobs']=[job];spec['job_count']=1
            path=root/'plan.json';path.write_text(json.dumps(spec));calls=[]
            def fake_rollout(provider,case,artifacts,seed,identity,dest):
                calls.append(identity)
                return {**case,'trajectory_id':identity,'rollout_id':0,'condition':'clean','fulfillable':True,
                    'failed':False,'unsafe':False,'partial_unsafe':False,'full_unsafe':False,'attack_success':False,'task_completed':True,
                    'tool_calls':[],'blocked_actions':[],'reasoning_trace':[],'rule_hits':[],'final_response':'fixture only'}
            class Provider:
                def __init__(self,*args,**kwargs):pass
                def invoke(self,*args,**kwargs):return {'choices':[{'message':{'content':'{"changes":[],"file_updates":[]}'}}]}
                def summary(self):return {'fixture':True,'physical_requests':0}
            with patch('flashtrustfulrsi.she_evolution.RecordedNativeProvider',Provider),patch('flashtrustfulrsi.she_evolution.rollout',fake_rollout):
                run_job(root,path,job['job_id'])
            output=root/'runs/fixture-only'/job['job_id']
            result=json.loads((output/'result.json').read_text());self.assertEqual(result['best']['round_id'],'R00')
            self.assertEqual(calls[-2:],["test-seed-c0000-r0","test-selected-c0000-r0"])
            self.assertTrue((output/'rounds/R00/applied_artifact_patch.json').exists())
            self.assertFalse(json.loads((output/'rounds/R01/best_update.json').read_text())['updated'])

if __name__=='__main__':unittest.main()
