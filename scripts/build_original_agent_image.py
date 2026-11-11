"""Add local Git and Bash binaries to a verified SDK rootfs, without host changes."""
import argparse,hashlib,json,re,subprocess,tarfile
from pathlib import Path


def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    p=argparse.ArgumentParser();p.add_argument('--base',type=Path,required=True);p.add_argument('--base-sha256',required=True)
    p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    if sha(a.base)!=a.base_sha256 or a.output.exists():raise ValueError('Base mismatch or output exists')
    binaries={Path('/usr/bin/git'),Path('/bin/bash')};members={};receipt={}
    for binary in sorted(binaries):
        members[str(binary).lstrip('/')]=binary.resolve()
        result=subprocess.run(['ldd',str(binary)],capture_output=True,text=True,check=True)
        if 'not found' in result.stdout:raise ValueError('Missing binary dependency')
        for text in re.findall(r'/[^\s()]+',result.stdout):members[text.lstrip('/')]=Path(text).resolve()
    with tarfile.open(a.base) as base,tarfile.open(a.output,'w') as dest:
        old={}
        for member in base:
            if member.name.startswith('/') or '..' in Path(member.name).parts:raise ValueError('Unsafe base')
            # Explicit binaries replace BusyBox aliases, if present.
            if member.name in ('bin/bash','usr/bin/git'):continue
            data=base.extractfile(member) if member.isfile() else None
            if member.name in members and member.isfile():
                raw=data.read();old[member.name]=hashlib.sha256(raw).hexdigest()
                import io
                data=io.BytesIO(raw)
            dest.addfile(member,data)
        for target,source in members.items():
            digest=sha(source)
            if target in old:
                if old[target]!=digest:raise ValueError('Conflicting ELF library: '+target)
                continue
            info=dest.gettarinfo(str(source),arcname=target);info.uid=info.gid=0;info.uname=info.gname='root';info.mtime=0
            with source.open('rb') as stream:dest.addfile(info,stream)
            receipt[target]=digest
    document={'base_sha256':a.base_sha256,'added_files':receipt,'rootfs_sha256':sha(a.output),'bytes':a.output.stat().st_size}
    a.output.with_suffix('.manifest.json').write_text(json.dumps(document,indent=2));print(json.dumps(document))


if __name__=='__main__':main()
