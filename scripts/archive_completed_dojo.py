"""Lossless verified archival of completed AgentDojo jobs to reduce inode use.

Only this project's completed, unlocked jobs are touched. Every archived regular
file is byte-hash checked before its unpacked copy is removed. Scientific result
and status/manifest stay at their original locations. No in-flight job is touched.
"""
import argparse,fcntl,hashlib,json,os,tarfile,time
from pathlib import Path

KEEP={'result.json','status.json','manifest.json','job.lock','process.log','evidence.tar.gz','evidence-index.json','frozen-labels.json','label-quality.json'}
def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        while data:=f.read(1024*1024):h.update(data)
    return h.hexdigest()

def archive_job(job):
    if not (job/'result.json').exists():return False
    with (job/'job.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return False
        if json.loads((job/'result.json').read_text()).get('status')!='completed':return False
        index=job/'evidence-index.json';archive=job/'evidence.tar.gz';temporary=job/'evidence.tar.gz.part'
        if index.exists():
            receipt=json.loads(index.read_text())
            if sha(archive)!=receipt['archive_sha256']:raise ValueError('Archived evidence changed')
            return False
        if archive.exists() or temporary.exists():raise ValueError('Partial archive requires explicit reconciliation: '+str(job))
        files=[]
        for path in sorted(job.rglob('*')):
            if path.is_symlink():raise ValueError('Unexpected symlink in completed job')
            if path.is_file() and path.name!='job.lock':files.append(path)
        hashes={str(p.relative_to(job)):sha(p) for p in files}
        with tarfile.open(temporary,'w:gz') as tar:
            for path in files:tar.add(path,arcname=str(path.relative_to(job)),recursive=False)
        with tarfile.open(temporary) as tar:
            for name,digest in hashes.items():
                data=tar.extractfile(name)
                if data is None or hashlib.file_digest(data,'sha256').hexdigest()!=digest:raise ValueError('Archive verification failed')
        with temporary.open('rb') as f:os.fsync(f.fileno())
        temporary.rename(archive)
        # The verified archive is now durable. All originals remain until here.
        removed=[]
        for path in files:
            relative=str(path.relative_to(job))
            if path.parent==job and path.name in KEEP:continue
            if sha(path)!=hashes[relative]:raise ValueError('Completed evidence mutated during archival')
            path.unlink();removed.append(relative)
        for path in sorted((p for p in job.rglob('*') if p.is_dir()),key=lambda p:len(p.parts),reverse=True):
            if not any(path.iterdir()):path.rmdir()
        index.write_text(json.dumps({'schema':'flashtrustfulrsi.evidence-archive.v1','archive':'evidence.tar.gz','archive_sha256':sha(archive),
            'files_sha256':hashes,'unpacked_files_removed':removed,'verification':'every archived file SHA256 verified before removal',
            'time':time.time()},indent=2))
        return True

def main():
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--phase',required=True);p.add_argument('--watch',action='store_true');a=p.parse_args()
    root=a.root.resolve();phase=root/'runs'/a.phase
    if not phase.is_dir() or not a.phase.startswith(('formal-dojo-','formal-cim-labels-','formal-cim-evaluation-')) or '/' in a.phase:raise ValueError('Only declared completed Dojo/CIM campaign permitted')
    while True:
        count=0
        for path in sorted(phase.glob('*/result.json')):count+=archive_job(path.parent)
        print(json.dumps({'archived_new_jobs':count,'phase':a.phase,'time':time.time()}),flush=True)
        if not a.watch:return
        time.sleep(30)

if __name__=='__main__':main()
