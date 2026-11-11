"""Extend the verified offline Python rootfs with hash-pinned SDK wheels."""
import argparse, hashlib, io, json, re, subprocess, tarfile, tempfile, zipfile
from pathlib import Path


def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    p=argparse.ArgumentParser();p.add_argument('--base',type=Path,required=True)
    p.add_argument('--base-sha256',required=True);p.add_argument('--wheel-lock',type=Path,required=True)
    p.add_argument('--wheelhouse',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    if sha(a.base)!=a.base_sha256:raise ValueError('Base rootfs hash mismatch')
    if a.output.exists():raise ValueError('Refusing to overwrite existing image archive')
    lock=json.loads(a.wheel_lock.read_text());libs={};names=set()
    with tarfile.open(a.output,'w') as dest, tarfile.open(a.base) as base, tempfile.TemporaryDirectory(prefix='ftrsi-agent-wheels-') as temp:
        for member in base:
            if member.name.startswith('/') or '..' in Path(member.name).parts:raise ValueError('Unsafe base member')
            dest.addfile(member,base.extractfile(member) if member.isfile() else None);names.add(member.name)
        for name,expected in lock.items():
            wheel=a.wheelhouse/name
            if sha(wheel)!=expected:raise ValueError('Wheel hash mismatch: '+name)
            with zipfile.ZipFile(wheel) as archive:
                for member in archive.infolist():
                    if member.is_dir():continue
                    rel=Path(member.filename)
                    if rel.is_absolute() or '..' in rel.parts:raise ValueError('Unsafe wheel member')
                    data=archive.read(member);info=tarfile.TarInfo('opt/site-packages/'+member.filename)
                    if info.name in names:raise ValueError('Overlapping rootfs member')
                    names.add(info.name);info.size=len(data);info.mode=0o644;dest.addfile(info,io.BytesIO(data))
                    if '.so' in rel.name:
                        path=Path(temp)/rel;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
                        result=subprocess.run(['ldd',str(path)],capture_output=True,text=True,check=True)
                        if 'not found' in result.stdout:raise ValueError('Unresolved wheel ELF dependency')
                        for item in re.findall(r'/[^\s()]+',result.stdout):
                            library=Path(item);target=item.lstrip('/')
                            if target not in names:
                                info=dest.gettarinfo(str(library.resolve()),arcname=target)
                                info.uid=info.gid=0;info.uname=info.gname='root';info.mtime=0
                                with library.open('rb') as stream:dest.addfile(info,stream)
                                names.add(target);libs[item]=sha(library)
        # Supply standard shell applets needed by the native file-editing tool.
        for command in ('mkdir','cp','mv','rm','find','grep','sort','cut','tr','tail','touch','chmod','pwd','printf','test','env','xargs','diff'):
            name='bin/'+command
            if name in names:continue
            info=tarfile.TarInfo(name);info.type=tarfile.SYMTYPE;info.linkname='/bin/busybox';info.mode=0o755;dest.addfile(info)
    manifest={'base_sha256':a.base_sha256,'wheels':lock,'additional_ELF_dependencies':libs,
        'rootfs_sha256':sha(a.output),'rootfs_bytes':a.output.stat().st_size}
    a.output.with_suffix('.manifest.json').write_text(json.dumps(manifest,indent=2));print(json.dumps(manifest))


if __name__=='__main__':main()
