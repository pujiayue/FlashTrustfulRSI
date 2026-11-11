import importlib.util
from pathlib import Path
import tempfile
import unittest

@unittest.skipUnless(importlib.util.find_spec('agentdojo'),'optional pinned native AgentDojo')
class DojoFollowupTests(unittest.TestCase):
    def test_official_native_pipeline_records_environments_and_restores_retry_wrapper(self):
        from agentdojo.task_suite.load_suites import get_suite
        from agentdojo.agent_pipeline.llms import openai_llm
        from flashtrustfulrsi.dojo_followup import episode
        import json
        seen=[]
        class Provider:
            def invoke(self,protocol,body,request_id):
                seen.append(body)
                message={'role':'assistant','content':'The bill was paid.'}
                if len(seen)==1:
                    message={'role':'assistant','content':None,'tool_calls':[{'id':'call_fixture','type':'function',
                        'function':{'name':'send_money','arguments':json.dumps({'recipient':'UK12345678901234567890','amount':98.70,'subject':'Car Rental','date':'2022-01-01'})}}]}
                return {'id':'fixture','object':'chat.completion','created':0,'model':'deepseek-flash',
                    'choices':[{'index':0,'finish_reason':'tool_calls' if len(seen)==1 else 'stop','message':message}],
                    'usage':{'prompt_tokens':1,'completion_tokens':1,'total_tokens':2}}
        original=openai_llm.chat_completion_request
        with tempfile.TemporaryDirectory() as tmp:
            row=episode(Provider(),get_suite('v1.2.2','banking'),'user_task_0',None,{},None,'fixture',Path(tmp),
                        memory=[],model='deepseek-flash',official=True)
        self.assertIs(openai_llm.chat_completion_request,original)
        self.assertEqual(row['status'],'finished');self.assertTrue(row['utility'])
        self.assertIsNone(row['attack_success'])
        self.assertNotEqual(row['initial_environment'],row['final_environment'])
        self.assertTrue(all(body['model']=='deepseek-flash' and 'seed' not in body for body in seen))
        self.assertTrue(all(isinstance(m['content'],(str,type(None))) for body in seen for m in body['messages']))
