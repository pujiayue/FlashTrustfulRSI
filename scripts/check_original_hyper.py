"""Native HyperAgents task/JSON-tool loop contracts in real offline containers."""
import argparse,base64,json,tempfile
from pathlib import Path
from flashtrustfulrsi.hyper_container import seed_hyper,run_hyper

class Fixture:
    def __init__(self,mode,compat=False):self.mode=mode;self.calls=0;self.compat=compat
    def invoke(self,protocol,body,request_id=None):
        assert protocol=='chat' and body['model']=='gpt-5.6-sol';self.calls+=1
        if self.mode=='task':text='<json>{"response":"def answer(): return 42"}</json>'
        elif self.calls==1:text='<json>'+json.dumps({'tool_name':'editor','tool_input':{'command':'create','path':'/tmp/repo/proof.py','file_text':'value = 42\n'}})+'</json>'
        else:
            assert 'tool_output' in body['messages'][-1]['content'];text='Completed the edit.'
        if self.compat:text=text.removeprefix('<json>').removesuffix('</json>')
        return {'id':'fixture','object':'chat.completion','created':0,'model':'gpt-5.6-sol','choices':[{'index':0,'message':{'role':'assistant','content':text},'finish_reason':'stop'}],'usage':{'prompt_tokens':0,'completion_tokens':0,'total_tokens':0}}

def main():
    p=argparse.ArgumentParser();p.add_argument('--source',required=True);p.add_argument('--image',required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--compat',action='store_true');a=p.parse_args()
    global run_hyper
    if a.compat:
        from flashtrustfulrsi.hyper_container_v2 import run_hyper
    with tempfile.TemporaryDirectory(prefix='ftrsi-hyper-check-') as tmp:
        seed=Path(tmp)/'seed';seed_hyper(a.source,seed)
        for mode in ('task','meta'):
            provider=Fixture(mode,a.compat);result=run_hyper(agent_dir=seed,request={'mode':mode,'inputs':{'domain':'fixture','task':'return 42'},'iterations_left':0},image=a.image,provider=provider,artifact_dir=a.output/mode,call_id='hyper-contract-'+mode)
            if a.compat:assert len(result['git_head'])==40
            if mode=='task':assert result['prediction']=='def answer(): return 42' and provider.calls==1
            else:assert base64.b64decode(result['files']['proof.py']['data'])==b'value = 42\n' and provider.calls==2
        report={'status':'passed','native_TaskAgent':True,'native_MetaAgent_and_editor':True,'bare_JSON_compatibility':a.compat,'model_calls':0,'fixture_calls':3,'scope':'contract_not_scientific_result'}
        (a.output/'contract-result.json').write_text(json.dumps(report,indent=2));print(json.dumps(report))

if __name__=='__main__':main()
