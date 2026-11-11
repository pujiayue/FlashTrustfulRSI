"""Execute an original coding agent snapshot against an answer-free task copy."""
import hashlib,json,shutil,tempfile
from pathlib import Path
from .native_container import InferenceSocket,NativeSandbox
from .provider import write_json


def files_sha(root):
    root=Path(root);result={}
    for p in sorted(root.rglob('*')):
        if p.is_symlink():raise ValueError('Unexpected snapshot symlink')
        if p.is_file():result[str(p.relative_to(root))]=hashlib.sha256(p.read_bytes()).hexdigest()
    return result


def run_original(*,agent_dir,task_dir,instructions,image,provider,artifact_dir,call_id,self_improve=False):
    artifact_dir=Path(artifact_dir);artifact_dir.mkdir(parents=True,exist_ok=True)
    request={'algorithm':'dgm','instructions':instructions,'self_improve':self_improve}
    frozen={'request':request,'agent_files':files_sha(agent_dir),'task_files':files_sha(task_dir),'image':image,
        'entry_sha256':hashlib.sha256(Path(__file__).with_name('original_entry.py').read_bytes()).hexdigest()}
    signature=hashlib.sha256(json.dumps(frozen,sort_keys=True).encode()).hexdigest()
    cached=artifact_dir/'result.json'
    if cached.exists():
        saved=json.loads(cached.read_text())
        if saved['input_sha256']!=signature:raise ValueError('Original agent replay changed')
        return saved['document']
    write_json(artifact_dir/'input.json',frozen)
    with tempfile.TemporaryDirectory(prefix='ftrsi-original-') as temp:
        root=Path(temp);bundle=root/'bundle';bundle.mkdir()
        shutil.copytree(agent_dir,bundle/'agent');shutil.copytree(task_dir,bundle/'task')
        for name in ('original_entry.py','native_entry.py'):shutil.copyfile(Path(__file__).with_name(name),bundle/name)
        write_json(bundle/'request.json',request)
        for p in [bundle,*bundle.rglob('*')]:p.chmod(0o755 if p.is_dir() or p.stat().st_mode&0o111 else 0o644)
        with InferenceSocket(root/'model.sock',provider.invoke,call_id) as socket:
            sandbox=NativeSandbox(image=image,model_socket=root/'model.sock')
            result=sandbox.run_process(['python','-B','/workspace/original_entry.py'],name='ftrsi-original-dgm',workdir=bundle,timeout=1800)
        write_json(artifact_dir/'process.json',{'returncode':result.returncode,'stderr':result.stderr,'model_calls':socket.calls})
        if result.returncode:raise RuntimeError('Original agent container failed: '+result.stderr[-2000:])
        document=json.loads(result.stdout)
        write_json(cached,{'input_sha256':signature,'document':document});return document


def seed_dgm(source,destination):
    """Use the original polyglot agent layout; exclude cached paper run results."""
    source=Path(source);destination=Path(destination)
    if destination.exists():raise ValueError('Seed directory exists')
    destination.mkdir(parents=True)
    for name in ('llm.py','llm_withtools.py','requirements.txt'):
        shutil.copyfile(source/name,destination/name)
    shutil.copyfile(source/'coding_agent_polyglot.py',destination/'coding_agent.py')
    for name in ('tools','prompts','utils'):
        shutil.copytree(source/name,destination/name,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    return files_sha(destination)
