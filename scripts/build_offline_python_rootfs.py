"""Build a small offline witness image from installed Linux Python and a pinned wheel.

Only reads Python standard-library files, ELF dependencies and static BusyBox.
Never copies home directories, host accounts, Docker sockets or credentials.
Output is a rootfs tar for `docker import`; no host packages are installed.
"""
import argparse, hashlib, io, json, os, platform, re, subprocess, sys, sysconfig, tarfile, zipfile
from pathlib import Path

def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--wheel',required=True)
    p.add_argument('--wheel-sha256',required=True);p.add_argument('--output',required=True);a=p.parse_args()
    if sys.version_info[:2]!=(3,12) or sys.platform!='linux':raise ValueError('Linux Python 3.12 required')
    out=Path(a.output);wheel=Path(a.wheel).resolve(strict=True)
    if out.exists():raise ValueError('Refusing to overwrite existing image archive')
    if digest(wheel)!=a.wheel_sha256:raise ValueError('Wheel SHA-256 mismatch')
    executable=Path(sys.executable).resolve();stdlib=Path(sysconfig.get_path('stdlib')).resolve()
    if not str(executable).startswith('/usr/') or not str(stdlib).startswith('/usr/lib/python3.12'):
        raise ValueError('This recipe expects distribution Python under /usr')
    sources={str(executable).lstrip('/'):executable}
    for f in stdlib.rglob('*'):
        if f.is_file() and not any(x in {'__pycache__','test','tests','site-packages','dist-packages'} for x in f.relative_to(stdlib).parts):
            sources[str(f).lstrip('/')]=f
    busybox=Path('/usr/bin/busybox')
    check=subprocess.run(['ldd',str(busybox)],capture_output=True,text=True)
    if 'not a dynamic executable' not in check.stdout+check.stderr and 'statically linked' not in check.stdout+check.stderr:
        raise ValueError('A static BusyBox is required')
    sources['bin/busybox']=busybox
    for f in list(sources.values()):
        if f==executable or '.so' in f.name:
            result=subprocess.run(['ldd',str(f)],capture_output=True,text=True,check=True)
            for name in re.findall(r'/[^\s()]+',result.stdout):
                library=Path(name)
                if not library.is_file():raise ValueError('Missing ELF dependency '+name)
                sources[name.lstrip('/')]=library
    for package in ('python3.12','python3.12-minimal','libpython3.12-stdlib','libc6','busybox-static'):
        path=Path('/usr/share/doc')/package/'copyright'
        if path.is_file():sources[str(path).lstrip('/')]=path
    if sum(f.stat().st_size for f in sources.values())>256*1024*1024:raise ValueError('Rootfs source exceeds 256 MiB recipe cap')
    out.parent.mkdir(parents=True,exist_ok=True);hashes={}
    with tarfile.open(out,'w',dereference=True) as tar:
        def add_bytes(name,content,mode=0o644):
            info=tarfile.TarInfo(name);info.size=len(content);info.mode=mode;tar.addfile(info,io.BytesIO(content))
        for name,path in sorted(sources.items()):
            hashes[name]=digest(path);info=tar.gettarinfo(str(path),arcname=name)
            info.uid=info.gid=0;info.uname=info.gname='root';info.mtime=0
            with path.open('rb') as stream:tar.addfile(info,stream)
        for folder,mode in (('tmp',0o1777),('workspace',0o755),('etc',0o755),('usr/local/bin',0o755)):
            info=tarfile.TarInfo(folder);info.type=tarfile.DIRTYPE;info.mode=mode;tar.addfile(info)
        for cmd in ('sh','sed','awk','cat','ls','id','sleep','true','false','head','wc'):
            info=tarfile.TarInfo('bin/'+cmd);info.type=tarfile.SYMTYPE;info.linkname='/bin/busybox';info.mode=0o755;tar.addfile(info)
        info=tarfile.TarInfo('usr/local/bin/python');info.type=tarfile.SYMTYPE;info.linkname=str(executable);info.mode=0o755;tar.addfile(info)
        add_bytes('etc/passwd',b'nobody:x:65534:65534:isolated witness:/nonexistent:/bin/false\n')
        add_bytes('etc/group',b'nogroup:x:65534:\n')
        with zipfile.ZipFile(wheel) as archive:
            for item in archive.infolist():
                if item.is_dir():continue
                rel=Path(item.filename)
                if rel.is_absolute() or '..' in rel.parts:raise ValueError('Invalid wheel member')
                add_bytes('opt/site-packages/'+item.filename,archive.read(item))
    manifest={'scope':'offline_witness_image','python':sys.version,'machine':platform.machine(),
              'source_files':hashes,'wheel':wheel.name,'wheel_sha256':a.wheel_sha256,'rootfs_sha256':digest(out),
              'rootfs_bytes':out.stat().st_size,'expected_import_env':['PATH=/usr/local/bin:/usr/bin:/bin','PYTHONPATH=/opt/site-packages']}
    out.with_suffix('.manifest.json').write_text(json.dumps(manifest,indent=2))
    print(json.dumps({k:v for k,v in manifest.items() if k!='source_files'},indent=2))

if __name__=='__main__':main()
