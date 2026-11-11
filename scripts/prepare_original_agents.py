"""Materialize the exact base versions and patches published by Trusting Trust.

Downloads only; does not import or execute agent code. Upstream licenses remain.
"""
import argparse,concurrent.futures,hashlib,json,stat,subprocess,urllib.request,zipfile
from pathlib import Path

SOURCES={'dgm':('jennyzzt/dgm','a565fd2'),
    'sica':('MaximeRobeyns/self_improving_coding_agent','ed8275d'),
    'hyperagents':('facebookresearch/HyperAgents','59a68f6')}


def get(url):
    return urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'FlashTrustfulRSI'}),timeout=180)


def main():
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1]);a=p.parse_args()
    root=a.root.resolve();out=root/'external/original-agents';out.mkdir(parents=True,exist_ok=True)
    def one(name):
        repo,short=SOURCES[name];receipt=out/(name+'-source.json')
        if receipt.exists():return json.loads(receipt.read_text())
        with get(f'https://api.github.com/repos/{repo}/commits/{short}') as response:commit=json.load(response)['sha']
        if not commit.startswith(short) or len(commit)!=40:raise ValueError('Unexpected resolved commit')
        archive=out/(name+'-'+commit+'.zip')
        if not archive.exists():
            size=0;temp=archive.with_suffix('.part')
            with get(f'https://codeload.github.com/{repo}/zip/{commit}') as response,temp.open('wb') as f:
                while chunk:=response.read(1024*1024):
                    size+=len(chunk)
                    if size>1024**3:raise ValueError('Source archive exceeds 1 GiB')
                    f.write(chunk)
            temp.replace(archive)
        dest=out/name
        # Refuse partial directories: inspect rather than overwrite unknown state.
        if dest.exists():raise ValueError('Incomplete source exists: '+str(dest))
        dest.mkdir()
        with zipfile.ZipFile(archive) as z:
            prefix=z.namelist()[0].split('/')[0]+'/'
            if sum(i.file_size for i in z.infolist())>2*1024**3:raise ValueError('Source exceeds 2 GiB')
            for info in z.infolist():
                if not info.filename.startswith(prefix):raise ValueError('Mixed archive roots')
                relative=Path(info.filename[len(prefix):])
                if relative.is_absolute() or '..' in relative.parts:raise ValueError('Unsafe member')
                if info.is_dir():continue
                target=dest/relative
                if not target.resolve().is_relative_to(dest):raise ValueError('Escaping member')
                target.parent.mkdir(parents=True,exist_ok=True)
                if stat.S_ISLNK(info.external_attr>>16):
                    link=z.read(info).decode()
                    if Path(link).is_absolute() or not (target.parent/link).resolve().is_relative_to(dest):raise ValueError('External symlink')
                    target.symlink_to(link)
                else:
                    target.write_bytes(z.read(info));target.chmod(0o755 if info.external_attr>>16&0o111 else 0o644)
        patch=root/'external/research/E08/modifications'/(name+'_modifications.patch')
        subprocess.run(['git','apply','--check',str(patch)],cwd=dest,check=True)
        subprocess.run(['git','apply',str(patch)],cwd=dest,check=True)
        hashes={str(f.relative_to(dest)):hashlib.sha256(f.read_bytes()).hexdigest()
            for f in sorted(dest.rglob('*')) if f.is_file() and not f.is_symlink()}
        result={'algorithm':name,'repo':'https://github.com/'+repo,'commit':commit,
            'archive_sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),
            'published_patch':str(patch.relative_to(root)),'patch_sha256':hashlib.sha256(patch.read_bytes()).hexdigest(),
            'patch_applied':True,'files_sha256':hashes,'executed':False}
        receipt.write_text(json.dumps(result,indent=2));return result
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        for result in pool.map(one,SOURCES):
            print(json.dumps({k:v for k,v in result.items() if k!='files_sha256'}),flush=True)


if __name__=='__main__':main()
