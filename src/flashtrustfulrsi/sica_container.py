"""Reusable native SICA execution boundary; no host code execution fallback."""
import hashlib,json,shutil,tempfile
from pathlib import Path
from .native_container import InferenceSocket,NativeSandbox
from .original_container import files_sha
from .provider import write_json

def seed_sica(source,destination):
 source=Path(source);destination=Path(destination)
 if destination.exists():raise ValueError('Seed already exists')
 destination.mkdir(parents=True)
 shutil.copytree(source/'base_agent',destination/'base_agent',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
 return files_sha(destination)

def run_sica(*,agent_dir,prompt,image,provider,artifact_dir,call_id,timeout=1800):
 if type(timeout)!=int or not 1<=timeout<=1800:raise ValueError('Invalid timeout')
 if not isinstance(prompt,str) or not prompt:raise ValueError('Prompt required')
 out=Path(artifact_dir);out.mkdir(parents=True,exist_ok=True)
 request={'prompt':prompt,'timeout':timeout}
 closure={n:hashlib.sha256(Path(__file__).with_name(n).read_bytes()).hexdigest() for n in ('sica_entry.py','native_entry.py','sica_container.py','native_container.py','sandbox.py')}
 frozen={'request':request,'image':image,'agent_files':files_sha(agent_dir),'adapter_files':closure}
 signature=hashlib.sha256(json.dumps(frozen,sort_keys=True).encode()).hexdigest();cache=out/'result.json'
 if cache.exists():
  data=json.loads(cache.read_text())
  if data['input_sha256']!=signature:raise ValueError('SICA replay changed')
  return data['document']
 if (out/'input.json').exists() and json.loads((out/'input.json').read_text())!=frozen:raise ValueError('SICA incomplete input changed')
 write_json(out/'input.json',frozen)
 with tempfile.TemporaryDirectory(prefix='ftrsi-sica-') as tmp:
  root=Path(tmp);bundle=root/'bundle';bundle.mkdir();shutil.copytree(agent_dir,bundle/'agent')
  for name in ('sica_entry.py','native_entry.py'):shutil.copyfile(Path(__file__).with_name(name),bundle/name)
  write_json(bundle/'request.json',request)
  for p in [bundle,*bundle.rglob('*')]:p.chmod(0o755 if p.is_dir() or p.stat().st_mode&0o111 else 0o644)
  errors=[]
  def invoke(*a,**k):
   try:return provider.invoke(*a,**k)
   except Exception as exc:errors.append(exc);raise
  with InferenceSocket(root/'model.sock',invoke,call_id) as socket:
   sandbox=NativeSandbox(image=image,model_socket=root/'model.sock')
   result=sandbox.run_process(['python','-B','/workspace/sica_entry.py'],name='ftrsi-sica-native',workdir=bundle,timeout=timeout+30)
  write_json(out/'process.json',{'returncode':result.returncode,'stderr':result.stderr,'inference_calls':socket.calls})
  if errors:raise RuntimeError('SICA inference failed; no valid result') from errors[0]
  if result.returncode:raise RuntimeError('SICA container failed: '+result.stderr[-2000:])
  document=json.loads(result.stdout)
  if document['answer'] is None:raise RuntimeError('SICA did not submit an answer')
  write_json(cache,{'input_sha256':signature,'document':document});return document
