import importlib.util,json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch


@unittest.skipUnless(importlib.util.find_spec('agentdojo'),'Pinned AgentDojo external dependency required')
class NativeDojoTests(unittest.TestCase):
    def test_native_tool_execution_score_and_recorded_transport(self):
        from flashtrustfulrsi.dojo_formal import plan,run_job
        from flashtrustfulrsi.provider import write_json
        actual=Path(os.environ.get('FTRSI_NATIVE_ROOT','.')).resolve()
        if not list((actual/'upstream').glob('ethz-spylab-agentdojo-*')):self.skipTest('Project upstream required')
        with tempfile.TemporaryDirectory(prefix='ftrsi-dojo-contract-') as d:
            root=Path(d);(root/'upstream').symlink_to(actual/'upstream',target_is_directory=True)
            (root/'configs').mkdir();(root/'configs/provider-v02.json').write_text(json.dumps({'model':'gpt-5.6-sol','base_url':'https://fixture.invalid/v1','secret_ref':str(root/'fixture.json')}))
            (root/'fixture.json').write_text(json.dumps({'api_key':'fixture-only','base_url':'https://fixture.invalid/v1'}))
            doc=plan(root,'contract');p=root/'plan.json';write_json(p,doc)
            common={'id':'fixture','object':'chat.completion','created':0,'model':'gpt-5.6-sol','usage':{'prompt_tokens':1,'completion_tokens':1,'total_tokens':2}}
            first={**common,'choices':[{'index':0,'finish_reason':'tool_calls','message':{'role':'assistant','content':None,'tool_calls':[{'id':'call_fixture','type':'function','function':{'name':'send_money','arguments':json.dumps({'recipient':'UK12345678901234567890','amount':98.70,'subject':'Car Rental','date':'2022-01-01'})}}]}}]}
            second={**common,'choices':[{'index':0,'finish_reason':'stop','message':{'role':'assistant','content':'The bill was paid.'}}]}
            with patch('urllib.request.urlopen') as network:
                network.return_value.__enter__.return_value.read.side_effect=[json.dumps(first).encode(),json.dumps(second).encode()]
                run_job(root,p,doc['jobs'][0]['job_id'])
                result=json.loads((root/'runs/contract'/doc['jobs'][0]['job_id']/'result.json').read_text())
                self.assertEqual(result['status'],'completed');self.assertTrue(result['utility'])
                self.assertEqual(network.call_count,2)
                self.assertTrue(any(m['role']=='tool' for m in result['messages']))
                self.assertTrue(list((root/'runs/contract').rglob('native-traces/**/*.json')))
                self.assertEqual(doc['job_count'],9729)


if __name__=='__main__':unittest.main()
