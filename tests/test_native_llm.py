"""Native upstream Worker contract; zero external model calls."""
import importlib.util,unittest
from flashtrustfulrsi.native_llm import bind_llm


@unittest.skipUnless(importlib.util.find_spec('flashrsi') and importlib.util.find_spec('openai'), 'optional native dependencies')
class NativeLLMTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_protocol_and_failure_do_not_retry(self):
        import openai
        from flashrsi.agents.llm import LLM
        calls=[]
        class Provider:
            def invoke(self,protocol,body,request_id):
                calls.append((protocol,body,request_id))
                if len(calls)==2:raise RuntimeError('recorded transport failure')
                return {'id':'fixture','object':'chat.completion','created':0,'model':'gpt-5.6-sol',
                    'choices':[{'index':0,'message':{'role':'assistant','content':'native worker result'},'finish_reason':'stop'}],
                    'usage':{'prompt_tokens':7,'completion_tokens':4,'total_tokens':11}}
        original=openai.AsyncOpenAI
        agent=bind_llm(LLM({'name':'llm','type':'llm','max_retries':0,'llm':{
            'model_name':'gpt-5.6-sol','api_key':'no-secret','base_url':'http://model-gateway/v1','max_tokens':32768}}),Provider())
        first=await agent.run('preserve original prompt')
        self.assertEqual(first.output,'native worker result')
        self.assertEqual(first.usage['total_tokens'],11)
        self.assertEqual(calls[0][0],'chat');self.assertEqual(calls[0][1]['max_tokens'],32768)
        self.assertEqual(calls[0][1]['messages'],[{'role':'user','content':'preserve original prompt'}])
        second=await agent.run('fail once')
        self.assertEqual(second.status,'error');self.assertEqual(len(calls),2)
        self.assertIs(openai.AsyncOpenAI,original)
        self.assertEqual([x[2] for x in calls],['text-worker-00000001','text-worker-00000002'])

if __name__=='__main__':unittest.main()
