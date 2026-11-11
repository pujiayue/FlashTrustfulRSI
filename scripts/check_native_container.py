"""Real container/native tool loop using explicit mocked model responses (no API)."""
import argparse,asyncio,json,tempfile
from pathlib import Path
from flashrsi.agents.miniagent import MiniAgent
from flashtrustfulrsi.native_container import bind_miniagent


class FixtureProvider:
    def __init__(self):self.calls=[]
    def invoke(self,protocol,body,request_id=None):
        self.calls.append({'protocol':protocol,'body':body,'request_id':request_id})
        if protocol!='responses':raise ValueError('Expected native GPT Responses protocol')
        if len(self.calls)==1:
            output=[{'type':'function_call','id':'fc_contract','call_id':'call_contract','name':'shell',
                'arguments':json.dumps({'command':"python -c \"from pathlib import Path; Path('proof.txt').write_text('native tool executed')\""}),'status':'completed'}]
        else:
            assert any(x.get('type')=='function_call_output' for x in body['input'])
            output=[{'type':'message','id':'msg_contract','role':'assistant','status':'completed',
                'content':[{'type':'output_text','text':'<DESIGN>Created proof.txt using the native shell tool.</DESIGN>','annotations':[]}]}]
        return {'id':'resp_contract_'+str(len(self.calls)),'object':'response','created_at':0,
            'status':'completed','model':'gpt-5.6-sol','output':output,
            'usage':{'input_tokens':0,'output_tokens':0,'total_tokens':0}}


async def run(args):
    provider=FixtureProvider()
    agent=MiniAgent({'name':'miniagent','type':'agent','max_turns':4,'timeout':120,
        'llm':{'model_name':'gpt-5.6-sol','api_key':'no-secret','base_url':'http://model-gateway/v1','max_tokens':4096}})
    bind_miniagent(agent,image=args.image,provider=provider,artifact_dir=args.output,role='worker')
    with tempfile.TemporaryDirectory(prefix='ftrsi-native-proof-') as temp:
        answer=await agent.edit(Path(temp),'Create proof.txt containing native tool executed. Return a DESIGN summary.')
        assert (Path(temp)/'proof.txt').read_text()=='native tool executed'
        assert len(provider.calls)==2
        report={'status':'passed','scope':'mocked_model_contract_not_real_experiment',
            'native_tool_execution':True,'roundtrip_file_edit':True,'model_calls':0,'fixture_responses':2,'answer':answer}
        (Path(args.output)/'contract-result.json').write_text(json.dumps(report,indent=2));print(json.dumps(report))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--image',required=True);p.add_argument('--output',required=True)
    asyncio.run(run(p.parse_args()))
