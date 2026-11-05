"""Fetch immutable upstream source trees and register them in this isolated venv.
Run with this project's .venv/bin/python. Dependencies come from requirements.lock.
This is source-mode use of selected FlashRSI modules, not installation of all extras.
"""
import hashlib,json,sysconfig,urllib.request,zipfile
from pathlib import Path
root=Path(__file__).resolve().parent;manifest=json.loads((root/'upstream.lock.json').read_text());out=root/'upstream';out.mkdir(exist_ok=True);paths=[]
for item in manifest:
 archive=out/(item['id']+'.zip')
 if not archive.exists():
  request=urllib.request.Request('https://api.github.com/repos/'+item['repo']+'/zipball/'+item['commit'],headers={'User-Agent':'FlashTrustfulRSI-bootstrap'})
  with urllib.request.urlopen(request,timeout=180) as response:archive.write_bytes(response.read())
 # GitHub-generated archive bytes may change; semantic pin is commit. Record digest locally.
 digest=hashlib.sha256(archive.read_bytes()).hexdigest()
 with zipfile.ZipFile(archive) as z:
  for name in z.namelist():
   if not (out/name).resolve().is_relative_to(out.resolve()):raise ValueError('Unsafe archive path')
  members=[n for n in z.namelist() if not any(p in {'runs','outputs','__pycache__','.git'} for p in Path(n).parts)]
  z.extractall(out,members=members);folder=out/z.namelist()[0].split('/')[0]
 item['local_archive_sha256']=digest;item['source_dir']=str(folder.relative_to(root))
 if item.get('import_root') is not None:paths.append(str((folder/item['import_root']).resolve()))
Path(sysconfig.get_paths()['purelib'],'flashtrustfulrsi-upstream.pth').write_text('\n'.join(paths)+'\n')
(root/'upstream.resolved.json').write_text(json.dumps(manifest,indent=2));print('Registered',len(paths),'fixed source trees. Keep upstream licenses alongside sources.')
