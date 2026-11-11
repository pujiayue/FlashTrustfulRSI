"""Container-only native HyperAgents TaskAgent/MetaAgent entry."""
import contextlib,json,os,shutil,sys
from pathlib import Path

def main():
    import httpx,openai
    native=openai.OpenAI
    class Client(native):
        def __init__(self,*args,**kwargs):
            kwargs.update(api_key='no-secret',base_url='http://model-gateway/v1',max_retries=0,
                http_client=httpx.Client(transport=httpx.HTTPTransport(uds='/model.sock'),timeout=600))
            super().__init__(*args,**kwargs)
    openai.OpenAI=Client
    request=json.loads(Path('/workspace/request.json').read_text())
    shutil.copytree('/workspace/agent','/tmp/repo');os.chdir('/tmp/repo');sys.path.insert(0,'/tmp/repo');sys.path.insert(0,'/workspace')
    from native_entry import export_files
    Path('/tmp/native').mkdir()
    with contextlib.redirect_stdout(sys.stderr):
        if request['mode']=='task':
            from task_agent import TaskAgent
            agent=TaskAgent(model='gpt-5.6-sol',chat_history_file='/tmp/native/trace.md')
            prediction,history=agent.forward(request['inputs'])
        elif request['mode']=='meta':
            from meta_agent import MetaAgent
            agent=MetaAgent(model='gpt-5.6-sol',chat_history_file='/tmp/native/trace.md')
            agent.forward('/tmp/repo','/tmp/repo/evaluation',iterations_left=request['iterations_left'])
            prediction=None;history=None
        else:raise ValueError('Unknown original HyperAgents mode')
    print(json.dumps({'algorithm':'HyperAgents','mode':request['mode'],'prediction':prediction,'history':history,
        'files':export_files(Path('/tmp/repo')),'native_log':Path('/tmp/native/trace.md').read_text()},ensure_ascii=False))

if __name__=='__main__':main()
