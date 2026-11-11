"""Native HyperAgents loops with an inference-only socket in offline containers."""
import hashlib,json,shutil,tempfile
from pathlib import Path
from .native_container import InferenceSocket,NativeSandbox
from .original_container import files_sha
from .provider import write_json

def seed_hyper(source,destination):
    source=Path(source);destination=Path(destination);destination.mkdir()
    for name in ('agent','utils'):
        shutil.copytree(source/name,destination/name,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    for name in ('task_agent.py','meta_agent.py','select_next_parent.py','README.md','LICENSE.md'):
        shutil.copyfile(source/name,destination/name)
    return files_sha(destination)

def run_hyper(*,agent_dir,request,image,provider,artifact_dir,call_id):
    artifact_dir=Path(artifact_dir);artifact_dir.mkdir(parents=True,exist_ok=True)
    frozen={'request':request,'agent_files':files_sha(agent_dir),'image':image,
        'entry_sha256':hashlib.sha256(Path(__file__).with_name('hyper_entry_v2.py').read_bytes()).hexdigest()}
    signature=hashlib.sha256(json.dumps(frozen,sort_keys=True).encode()).hexdigest();cached=artifact_dir/'result.json'
    if cached.exists():
        saved=json.loads(cached.read_text())
        if saved['input_sha256']!=signature:raise ValueError('HyperAgents replay inputs changed')
        return saved['document']
    write_json(artifact_dir/'input.json',frozen)
    with tempfile.TemporaryDirectory(prefix='ftrsi-hyper-') as tmp:
        root=Path(tmp);bundle=root/'bundle';bundle.mkdir();shutil.copytree(agent_dir,bundle/'agent')
        for name in ('hyper_entry_v2.py','native_entry.py'):shutil.copyfile(Path(__file__).with_name(name),bundle/name)
        write_json(bundle/'request.json',request)
        for p in [bundle,*bundle.rglob('*')]:p.chmod(0o755 if p.is_dir() or p.stat().st_mode&0o111 else 0o644)
        failures=[]
        def invoke(*args,**kwargs):
            try:return provider.invoke(*args,**kwargs)
            except Exception as exc:failures.append(exc);raise
        with InferenceSocket(root/'model.sock',invoke,call_id) as socket:
            sandbox=NativeSandbox(image=image,model_socket=root/'model.sock')
            result=sandbox.run_process(['python','-B','/workspace/hyper_entry_v2.py'],name='ftrsi-hyper-'+request['mode'],workdir=bundle,timeout=1800)
        write_json(artifact_dir/'process.json',{'returncode':result.returncode,'stderr':result.stderr,'model_calls':socket.calls})
        if failures:raise RuntimeError('HyperAgents inference transport failed') from failures[0]
        if result.returncode:raise RuntimeError('HyperAgents container failed: '+result.stderr[-2000:])
        document=json.loads(result.stdout);write_json(cached,{'input_sha256':signature,'document':document});return document
