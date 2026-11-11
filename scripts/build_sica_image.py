"""Build SICA's offline image from verified project rootfs and wheel installation."""
import argparse,hashlib,json,re,subprocess,tarfile
from pathlib import Path

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
 p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);a=p.parse_args();r=a.root.resolve()
 base=r/'images/original-agent-v04.tar';manifest=json.loads(base.with_suffix('.manifest.json').read_text())
 if sha(base)!=manifest['rootfs_sha256']:raise ValueError('Base image changed')
 site=r/'.venv-sica/lib/python3.12/site-packages';cache=r/'cache/sica-tokenizer';out=r/'images/sica-agent-v04.tar'
 if out.exists():raise ValueError('Refusing to overwrite image')
 if not list(cache.iterdir()):raise ValueError('Offline tokenizer missing')
 additions={}
 for folder,target in [(site,'opt/site-packages'),(cache,'opt/tiktoken')]:
  for f in sorted(folder.rglob('*')):
   if '__pycache__' in f.parts or f.suffix=='.pyc':continue
   if f.is_symlink():raise ValueError('Unexpected dependency symlink')
   if f.is_file():additions[target+'/'+str(f.relative_to(folder))]=f
 # Native extension dependencies outside the wheel tree (bundled wheel libraries
 # are already copied, preserving their relative RPATH layout).
 for f in list(additions.values()):
  if '.so' not in f.name:continue
  result=subprocess.run(['ldd',str(f)],text=True,capture_output=True,check=True)
  if 'not found' in result.stdout:raise ValueError('Unresolved ELF dependency '+str(f))
  for name in re.findall(r'/[^\s()]+',result.stdout):
   path=Path(name)
   if not path.resolve().is_relative_to(site):additions[name.lstrip('/')]=path.resolve()
 hashes={};seen=set()
 with tarfile.open(base) as old,tarfile.open(out,'w') as new:
  for m in old:
   if m.name.startswith('opt/site-packages/'):continue
   if m.name in additions:
    if m.isfile() and hashlib.sha256(old.extractfile(m).read()).hexdigest()!=sha(additions[m.name]):raise ValueError('ELF conflict '+m.name)
    seen.add(m.name)
   new.addfile(m,old.extractfile(m) if m.isfile() else None)
  for name,f in additions.items():
   hashes[name]=sha(f)
   if name in seen:continue
   info=new.gettarinfo(str(f),arcname=name);info.uid=info.gid=0;info.uname=info.gname='root';info.mtime=0
   with f.open('rb') as data:new.addfile(info,data)
 doc={'base_sha256':sha(base),'rootfs_sha256':sha(out),'added_files':hashes,'tokenizer':'real cl100k_base offline cache','size':out.stat().st_size}
 out.with_suffix('.manifest.json').write_text(json.dumps(doc,indent=2));print(json.dumps({k:v for k,v in doc.items() if k!='added_files'}))
if __name__=='__main__':main()
