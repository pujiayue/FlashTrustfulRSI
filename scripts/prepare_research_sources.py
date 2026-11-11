"""Fetch complete commit-pinned research releases, including their reproduction assets.

Files remain external dependencies under their original licenses. Nothing is run.
"""
import argparse,concurrent.futures,hashlib,json,stat,urllib.request,zipfile
from pathlib import Path


def main():
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1])
    p.add_argument('--methods',default='E03,E05,E06,E07,E08,E10');a=p.parse_args()
    root=a.root.resolve();catalog=json.loads((root/'configs/catalog.json').read_text())['methods']
    wanted=a.methods.split(',');entries={r['id']:r for r in catalog}
    if set(wanted)-entries.keys():raise ValueError('Unknown method')
    out=root/'external/research';out.mkdir(parents=True,exist_ok=True)
    def fetch(mid):
        item=entries[mid];repo=item['repo'].removeprefix('https://github.com/')
        archive=out/(mid+'-'+item['commit']+'.zip');dest=out/mid;receipt=out/(mid+'-source.json')
        if receipt.exists():
            old=json.loads(receipt.read_text())
            if old['commit']!=item['commit']:raise ValueError('Existing source has different commit')
            return {'method':mid,'status':'existing','files':old['files']}
        if not archive.exists():
            request=urllib.request.Request(f'https://codeload.github.com/{repo}/zip/{item["commit"]}',headers={'User-Agent':'FlashTrustfulRSI'})
            temp=archive.with_suffix('.part');size=0
            with urllib.request.urlopen(request,timeout=180) as response,temp.open('wb') as stream:
                while chunk:=response.read(1024*1024):
                    size+=len(chunk)
                    if size>1024**3:raise ValueError('Source archive exceeds 1 GiB')
                    stream.write(chunk)
            temp.replace(archive)
        with zipfile.ZipFile(archive) as z:
            prefix=z.namelist()[0].split('/')[0]+'/'
            if sum(i.file_size for i in z.infolist())>2*1024**3:raise ValueError('Extracted source exceeds 2 GiB')
            files=0
            for info in z.infolist():
                if not info.filename.startswith(prefix):raise ValueError('Mixed archive roots')
                relative=Path(info.filename[len(prefix):])
                if relative.is_absolute() or '..' in relative.parts:raise ValueError('Unsafe archive member')
                if info.is_dir():continue
                target=dest/relative
                if not target.resolve().is_relative_to(dest.resolve()):raise ValueError('Archive path escapes source root')
                target.parent.mkdir(parents=True,exist_ok=True)
                if stat.S_ISLNK(info.external_attr>>16):
                    link=z.read(info).decode()
                    if Path(link).is_absolute() or not (target.parent/link).resolve().is_relative_to(dest.resolve()):raise ValueError('External source symlink rejected')
                    if target.is_symlink():
                        if str(target.readlink())!=link:raise ValueError('Existing symlink changed')
                    else:target.symlink_to(link)
                else:target.write_bytes(z.read(info))
                files+=1
        doc={'method':mid,'repo':repo,'commit':item['commit'],'files':files,
            'archive_sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),'license':item['license'],
            'scope':'complete_external_source_release_not_executed','directory':str(dest.relative_to(root))}
        receipt.write_text(json.dumps(doc,indent=2));return doc
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        for result in pool.map(fetch,wanted):print(json.dumps(result),flush=True)


if __name__=='__main__':main()
