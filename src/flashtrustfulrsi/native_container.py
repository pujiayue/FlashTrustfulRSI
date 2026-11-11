"""Bind FlashRSI MiniAgent Target/Worker calls to resource-limited containers.

The only host service exposed to candidate code accepts exact-model inference.
It has no file operation, shell, Docker or arbitrary network forwarding endpoint.
"""
import asyncio, base64, hashlib, json, os, re, shutil, socketserver, stat, tempfile, threading, types
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from .provider import write_json
from .sandbox import DockerSandbox


class NativeSandbox(DockerSandbox):
    def __init__(self,*args,model_socket=None,**kwargs):
        super().__init__(*args,**kwargs);self.model_socket=Path(model_socket).resolve()
        if not stat.S_ISSOCK(self.model_socket.stat().st_mode):raise ValueError('Expected a Unix inference socket')
        if any(c in str(self.model_socket) for c in ',\n'):raise ValueError('Invalid socket path')
    def command(self,command,**kwargs):
        args=super().command(command,**kwargs);index=args.index(self.image)
        return args[:index]+['--mount',f'type=bind,source={self.model_socket},target=/model.sock,readonly']+args[index:]


class InferenceSocket:
    def __init__(self,path,invoke,request_prefix,max_calls=128,*,model='gpt-5.6-sol'):
        self.path=Path(path);self.invoke=invoke;self.prefix=request_prefix;self.max_calls=max_calls;self.calls=0
        self.model=model
    def __enter__(self):
        owner=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                try:
                    protocols={'/v1/chat/completions':'chat','/v1/responses':'responses'}
                    if self.path not in protocols:raise ValueError('Unsupported inference route')
                    n=int(self.headers.get('Content-Length','0'))
                    if not 0<n<=2*1024*1024:raise ValueError('Request size outside contract')
                    body=json.loads(self.rfile.read(n))
                    if body.get('model')!=owner.model or body.get('stream'):raise ValueError('Unsupported model or streaming')
                    if owner.calls>=owner.max_calls:raise ValueError('Invocation request limit exceeded')
                    owner.calls+=1
                    response=owner.invoke(protocols[self.path],body,request_id=f'{owner.prefix}-{owner.calls:04d}')
                    data=json.dumps(response).encode();self.send_response(200)
                except Exception as exc:
                    data=json.dumps({'error':{'message':type(exc).__name__,'type':'ftrsi_inference_error'}}).encode();self.send_response(400)
                self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
        # A pre-existing socket is never removed or replaced.
        self.server=socketserver.UnixStreamServer(str(self.path),Handler);self.path.chmod(0o666)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start();return self
    def __exit__(self,*args):
        self.server.shutdown();self.server.server_close();self.thread.join();self.path.unlink()


def apply_export(directory,files):
    root=Path(directory).resolve();decoded={};size=0
    if not isinstance(files,dict) or len(files)>1000:raise ValueError('Invalid candidate files')
    for name,row in files.items():
        rel=Path(name)
        if rel.is_absolute() or '..' in rel.parts or not rel.parts or any(x in {'.git','__pycache__'} for x in rel.parts):raise ValueError('Unsafe candidate path')
        target=root/rel
        if not target.resolve().is_relative_to(root):raise ValueError('Candidate escapes checkout')
        data=base64.b64decode(row['data'],validate=True);size+=len(data)
        if len(data)>512*1024 or size>2*1024*1024:raise ValueError('Candidate size exceeds limit')
        mode=row['mode']
        if type(mode)!=int or mode&~0o777:raise ValueError('Invalid candidate mode')
        decoded[name]=(data,mode)
    existing=list(root.rglob('*'));readonly=set()
    for path in existing:
        if path.is_symlink():raise ValueError('Editing checkout contains symlink')
        if path.is_file() and not path.stat().st_mode&stat.S_IWUSR:
            name=path.relative_to(root).as_posix()
            replacement=decoded.get(name)
            if replacement is None or replacement[0]!=path.read_bytes() or (replacement[1]^path.stat().st_mode)&0o111:
                raise ValueError(f'Candidate changed read-only checkout file: {name}')
            readonly.add(name)
    # This is the native algorithm's disposable checkout, never its source tree.
    for path in existing:
        if path.is_file() and str(path.relative_to(root)) not in decoded:path.unlink()
    for name,(data,mode) in decoded.items():
        if name in readonly:continue
        target=root/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data);target.chmod(mode)


def bind_miniagent(agent,*,image,provider,artifact_dir,role):
    from flashrsi.core import Trajectory
    if agent.config['llm'].get('api_key')!='no-secret' or agent.config['llm'].get('base_url')!='http://model-gateway/v1':
        raise ValueError('Container config must use the credential-free local gateway')
    if agent.config.get('web'):raise ValueError('External web service integration requires a separately declared gateway')
    if not re.fullmatch('[a-z][a-z0-9-]{0,20}',role):raise ValueError('Invalid native role')
    dest=Path(artifact_dir);dest.mkdir(parents=True,exist_ok=True);sequence=0
    def execute(instance,prompt,working_dir):
        nonlocal sequence
        sequence+=1;call=f'{role}-{sequence:06d}';out=dest/call;out.mkdir(exist_ok=True)
        edit_inputs={str(p.relative_to(working_dir)):{'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'mode':p.stat().st_mode&0o777}
            for p in sorted(Path(working_dir).rglob('*')) if p.is_file()} if working_dir is not None else None
        frozen={'prompt':prompt,'snapshot_id':instance.snapshot.id,'config':dict(instance.config),'editing':working_dir is not None,'edit_inputs':edit_inputs}
        signature=hashlib.sha256(json.dumps(frozen,sort_keys=True,default=dict).encode()).hexdigest()
        cached=out/'result.json'
        if cached.exists():
            stored=json.loads(cached.read_text())
            if stored['input_sha256']!=signature:raise ValueError('Invocation replay changed')
            document=stored['document']
        else:
            write_json(out/'input.json',frozen)
            with tempfile.TemporaryDirectory(prefix='ftrsi-native-') as temp:
                root=Path(temp);bundle=root/'bundle';bundle.mkdir();shutil.copytree(instance.files_dir,bundle/'harness')
                if working_dir is not None:shutil.copytree(working_dir,bundle/'edit')
                config=instance.config
                request={'question':prompt,'llm_config':dict(config['llm']),'web_config':dict(config.get('web',{})),
                    'max_turns':config.get('max_turns',10),'editing':working_dir is not None,'debug_llm_io':False}
                if config.get('_worker_system_prompt'):request['system_prompt_append']=config['_worker_system_prompt']
                write_json(bundle/'request.json',request);shutil.copyfile(Path(__file__).with_name('native_entry.py'),bundle/'entry.py')
                for path in [bundle,*bundle.rglob('*')]:
                    original_mode=path.stat().st_mode
                    mode=0o755 if path.is_dir() or original_mode&0o111 else 0o644
                    if path.is_file() and path.is_relative_to(bundle/'edit') and not original_mode&stat.S_IWUSR:
                        mode&=~0o222
                    path.chmod(mode)
                with InferenceSocket(root/'model.sock',provider.invoke,call,model=config['llm']['model_name']) as server:
                    sandbox=NativeSandbox(image=image,model_socket=root/'model.sock')
                    result=sandbox.run_process(['python','-B','/workspace/entry.py'],name='ftrsi-native-'+call,
                        workdir=bundle,timeout=config.get('timeout',1800))
                write_json(out/'process.json',{'returncode':result.returncode,'stderr':result.stderr,'inference_calls':server.calls})
                if result.returncode:raise RuntimeError('Native container failed: '+result.stderr[-1500:])
                document=json.loads(result.stdout)
                write_json(cached,{'input_sha256':signature,'document':document})
        if working_dir is not None:apply_export(working_dir,document['edited_files'])
        native=document['native_result'];success=native['stop_reason']=='completed'
        return Trajectory(status='finished' if success else 'error',output=native['answer'] if success else '',
            messages=native.get('messages',[]),usage={'model_calls':native['usage'],'turns':native['turns']},
            error_message=None if success else native['stop_reason'])
    async def run(instance,prompt,working_dir=None):
        task=asyncio.create_task(asyncio.to_thread(execute,instance,prompt,working_dir))
        try:return await asyncio.shield(task)
        except asyncio.CancelledError:
            # Keep the native checkout alive until the bounded container exits.
            await task
            raise
    agent._run=types.MethodType(run,agent)
    return agent
