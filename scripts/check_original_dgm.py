"""Original DGM tool-loop/container contract; mocked responses, zero model calls."""
import argparse,base64,json,tempfile
from pathlib import Path
from flashtrustfulrsi.original_container import run_original,seed_dgm


class Fixture:
    def __init__(self):self.calls=0
    def invoke(self,protocol,body,request_id=None):
        self.calls+=1
        assert protocol=='responses' and body['model']=='gpt-5.6-sol'
        if self.calls==1:
            output=[{'type':'function_call','id':'fc_dgm','call_id':'call_dgm','name':'editor','arguments':json.dumps({'command':'create','path':'/tmp/task/proof.py','file_text':'value = 42\n'}),'status':'completed'}]
        else:
            assert any(m.get('type')=='function_call_output' for m in body['input'])
            output=[{'type':'message','id':'msg_dgm','role':'assistant','status':'completed','content':[{'type':'output_text','text':'Created the file.','annotations':[]}]}]
        return {'id':'resp_dgm_'+str(self.calls),'object':'response','created_at':0,'status':'completed','model':'gpt-5.6-sol','output':output,'usage':{'input_tokens':0,'output_tokens':0,'total_tokens':0}}


def main():
    p=argparse.ArgumentParser();p.add_argument('--source',required=True);p.add_argument('--image',required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    with tempfile.TemporaryDirectory(prefix='ftrsi-dgm-check-') as d:
        root=Path(d);seed_dgm(a.source,root/'agent');(root/'task').mkdir();(root/'task/README.md').write_text('Create proof.py with value = 42.')
        provider=Fixture();result=run_original(agent_dir=root/'agent',task_dir=root/'task',instructions='Create proof.py with value = 42.',image=a.image,provider=provider,artifact_dir=a.output,call_id='dgm-contract')
        assert provider.calls==2 and base64.b64decode(result['files']['proof.py']['data'])==b'value = 42\n'
        report={'status':'passed','original_DGM_tool_loop':True,'native_editor':True,'fixture_responses':2,'model_calls':0,'scope':'mocked_model_contract_not_evolution_experiment'}
        (a.output/'contract-result.json').write_text(json.dumps(report,indent=2));print(json.dumps(report))


if __name__=='__main__':main()
