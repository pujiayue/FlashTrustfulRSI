"""Immutable container bootstrap; execute the selected native MiniAgent runtime.

Only transport construction is adapted: HTTP travels over a mounted Unix socket.
The snapshot's original agent loop, prompts, tools, middleware and skill loader run.
"""
import asyncio, base64, contextlib, json, os, shutil, sys
from pathlib import Path


def export_files(root):
    files={};size=0
    for path in sorted(root.rglob('*')):
        if path.is_symlink():raise ValueError('Symlinks may not leave an editing container')
        if not path.is_file():continue
        relative=path.relative_to(root)
        if any(part in {'.git','__pycache__'} for part in relative.parts) or path.suffix in {'.pyc','.pyo'}:continue
        data=path.read_bytes();size+=len(data)
        if len(data)>512*1024 or size>2*1024*1024 or len(files)>=1000:raise ValueError('Candidate export exceeds limits')
        files[str(relative)]={'data':base64.b64encode(data).decode(),'mode':path.stat().st_mode&0o777}
    return files


async def main():
    import httpx,openai
    NativeAsyncClient=openai.AsyncOpenAI
    class SocketClient(NativeAsyncClient):
        def __init__(self,*args,**kwargs):
            kwargs['max_retries']=0
            kwargs['http_client']=httpx.AsyncClient(transport=httpx.AsyncHTTPTransport(uds='/model.sock'),timeout=600)
            super().__init__(*args,**kwargs)
    openai.AsyncOpenAI=SocketClient
    request=json.loads(Path('/workspace/request.json').read_text())
    shutil.copytree('/workspace/harness','/tmp/harness')
    if request.pop('editing'):
        shutil.copytree('/workspace/edit','/tmp/task');editing=True
    else:Path('/tmp/task').mkdir();editing=False
    os.chdir('/tmp/task');sys.path.insert(0,'/tmp/harness')
    request.update(workspace_dir='/tmp/harness',runs_root='/tmp/runs',working_dir='/tmp/task')
    # User code/tool output is bounded by the parent and cannot corrupt JSON stdout.
    with contextlib.redirect_stdout(sys.stderr):
        from runtime.agent import run
        result=await run(**request)
    output={'native_result':result,'edited_files':export_files(Path('/tmp/task')) if editing else None}
    print(json.dumps(output,ensure_ascii=False))


if __name__=='__main__':asyncio.run(main())
