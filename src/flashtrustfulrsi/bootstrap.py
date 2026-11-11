"""Prepare the pinned FlashRSI source unchanged; installs no Python dependencies."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import stat
import sysconfig
import tempfile
import urllib.request
import zipfile
from .provider import write_json


def source_lock(root):
    path=Path(root)/'configs/flashrsi-source.lock.json'
    if not path.exists():path=Path(sysconfig.get_path('data'))/'share/flashtrustfulrsi/flashrsi-source.lock.json'
    return json.loads(path.read_text())


def verify(path,lock):
    path=Path(path);bad=[]
    for name,row in lock['files'].items():
        p=path/name
        if p.is_symlink() or not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest()!=row['sha256']:bad.append(name)
    if bad:raise ValueError('Pinned native source mismatch: '+', '.join(bad[:8]))
    return {'status':'verified','path':str(path.resolve()),'repo':lock['repo'],'commit':lock['commit'],
            'files':len(lock['files']),'license':lock['license'],'modified_upstream_files':0}


def prepare(root,archive=None,verify_only=False):
    root=Path(root).resolve();lock=source_lock(root);dest=root/lock['destination']
    if dest.exists():return verify(dest,lock)
    if verify_only:raise FileNotFoundError('Native source not prepared: '+str(dest))
    dest.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='ftrsi-upstream-',dir=dest.parent) as temporary:
        temp=Path(temporary);zip_path=Path(archive).resolve() if archive else temp/'source.zip'
        if archive is None:
            request=urllib.request.Request(lock['url'],headers={'User-Agent':'FlashTrustfulRSI-source-bootstrap'})
            with urllib.request.urlopen(request,timeout=60) as response,zip_path.open('wb') as output:
                total=0
                while data:=response.read(1024*1024):
                    total+=len(data)
                    if total>64*1024*1024:raise ValueError('Native source archive exceeds the download limit')
                    output.write(data)
        stage=temp/'source';stage.mkdir();seen=set()
        with zipfile.ZipFile(zip_path) as source:
            if sum(info.file_size for info in source.infolist())>128*1024*1024:raise ValueError('Native source archive too large')
            for info in source.infolist():
                if info.is_dir():continue
                parts=Path(info.filename).parts
                if len(parts)<2 or Path(info.filename).is_absolute() or '..' in parts:raise ValueError('Unsafe native source path')
                name='/'.join(parts[1:])
                if name not in lock['files'] or name in seen:raise ValueError('Unexpected or duplicate native source member')
                row=lock['files'][name];data=source.read(info)
                if (stat.S_ISLNK(info.external_attr>>16) or row['mode']=='120000' or len(data)!=row['bytes']
                        or hashlib.sha256(data).hexdigest()!=row['sha256']):raise ValueError('Unverified native member: '+name)
                target=stage/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
                target.chmod(0o755 if row['mode']=='100755' else 0o644);seen.add(name)
        if seen!=set(lock['files']):raise ValueError('Native archive is incomplete')
        verify(stage,lock);stage.rename(dest)
    return verify(dest,lock)


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',default='.')
    p.add_argument('--archive',type=Path);p.add_argument('--verify-only',action='store_true')
    args=p.parse_args(argv);report=prepare(args.root,args.archive,args.verify_only)
    print(json.dumps({**report,'model_requests':0,'dependency_install_performed':False,
        'next':'Install the pinned upstream project in your own environment or explicitly bind its import path; inspect selected benchmark data/image separately.'},indent=2))
if __name__=='__main__':main()
