"""Container-only entry for the original DGM coding agent (not MiniAgent)."""
import contextlib,json,os,shutil,subprocess,sys
from pathlib import Path


def main():
    import httpx,openai
    NativeClient=openai.OpenAI
    class SocketClient(NativeClient):
        def __init__(self,*args,**kwargs):
            kwargs.update(api_key='no-secret',base_url='http://model-gateway/v1',max_retries=0,
                http_client=httpx.Client(transport=httpx.HTTPTransport(uds='/model.sock'),timeout=600))
            super().__init__(*args,**kwargs)
    openai.OpenAI=SocketClient
    request=json.loads(Path('/workspace/request.json').read_text())
    if request['algorithm']!='dgm':raise ValueError('Original algorithm backend not implemented')
    shutil.copytree('/workspace/agent','/tmp/agent');shutil.copytree('/workspace/task','/tmp/task')
    os.chdir('/tmp/task');sys.path.insert(0,'/tmp/agent');sys.path.insert(0,'/workspace')
    from native_entry import export_files
    for command in (['git','init','-q'],['git','add','.'],['git','-c','user.name=FlashTrustfulRSI','-c','user.email=local@example.invalid','commit','-qm','answer-free task base']):
        subprocess.run(command,check=True,capture_output=True)
    commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()
    with contextlib.redirect_stdout(sys.stderr):
        from coding_agent import AgenticSystem
        agent=AgenticSystem(request['instructions'],'/tmp/task',commit,chat_history_file='/tmp/native/log.md',
            self_improve=request.get('self_improve',False),language='python')
        if agent.code_model!='gpt-5.6-sol':raise ValueError('Unapproved model in original agent')
        agent.forward()
    patch=subprocess.check_output(['git','diff','HEAD'],text=True)
    print(json.dumps({'algorithm':'dgm','files':export_files(Path('/tmp/task')),'patch':patch,
        'native_log':Path('/tmp/native/log.md').read_text(),'base_commit':commit},ensure_ascii=False))


if __name__=='__main__':main()
