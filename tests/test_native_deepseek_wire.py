import unittest

from flashtrustfulrsi.dgm_deepseek_wire import response_envelope
from flashtrustfulrsi.searchqa_deepseek_dev import minia_response_payload


def function_call(identity):
    return {'type':'function','id':identity,'function':{'name':'editor','arguments':'{}'}}


class NativeDeepSeekWireTests(unittest.TestCase):
    def test_dgm_one_tool_and_miniagent_parallel_round_trip(self):
        vendor = {'id':'r','created':1,'model':'deepseek-flash',
                  'usage':{'prompt_tokens':17,'completion_tokens':5},
                  'choices':[{'finish_reason':'tool_calls','message':{'content':'Work',
                              'tool_calls':[function_call('one'),function_call('two')]}}]}
        sequential = response_envelope(vendor)
        self.assertEqual([item['type'] for item in sequential['output']], ['message','function_call'])
        parallel = response_envelope(vendor,parallel=True)
        self.assertEqual(parallel['usage']['total_tokens'], 22)
        first = {'type':'function_call_output','call_id':'one','output':'first result'}
        second = {'type':'function_call_output','call_id':'two','output':'second result'}
        body = {'model':'gpt-5.6-sol','instructions':'System','max_output_tokens':6000,'store':False,
                'include':['reasoning.encrypted_content'],'input':[{'role':'user','content':'Question'},
                *parallel['output'],first,second,first,second,{'role':'user','content':'Finish.'}]}
        result = minia_response_payload(body,4096)
        self.assertEqual(result['max_tokens'], 4096)
        self.assertEqual([message['role'] for message in result['messages']], ['system','user','assistant','tool','tool','user'])
        self.assertEqual([tool['id'] for tool in result['messages'][2]['tool_calls']], ['one','two'])
        body['input'][-2] = {**second,'output':'different result'}
        with self.assertRaisesRegex(ValueError,'Unpaired'):
            minia_response_payload(body,4096)
