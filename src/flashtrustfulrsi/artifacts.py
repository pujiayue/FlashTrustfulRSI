"""Completed-job evidence archives with a queryable SQLite result index."""
import argparse,fcntl,hashlib,io,json,os,shutil,sqlite3,tarfile,time,uuid
from pathlib import Path

TERMINAL={'completed','completed_with_execution_error'}

def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        while chunk:=f.read(1024*1024):h.update(chunk)
    return h.hexdigest()

def archived_result(phase,job_id):
    path=Path(phase)/'completed-index.sqlite3'
    if not path.exists():return None
    db=sqlite3.connect(f'file:{path}?mode=ro',uri=True)
    try:row=db.execute('SELECT result FROM jobs WHERE job=?',(job_id,)).fetchone()
    finally:db.close()
    return json.loads(row[0]) if row else None

def completed_jobs(phase):
    phase=Path(phase);jobs={p.parent.name for p in phase.glob('*/result.json')}
    path=phase/'completed-index.sqlite3'
    if path.exists():
        db=sqlite3.connect(f'file:{path}?mode=ro',uri=True)
        try:jobs.update(r[0] for r in db.execute('SELECT job FROM jobs'))
        finally:db.close()
    return jobs

def results(phase):
    phase=Path(phase)
    for job in sorted(completed_jobs(phase)):
        path=phase/job/'result.json'
        yield job,json.loads(path.read_text()) if path.exists() else archived_result(phase,job)

def read_artifact(phase,job_id,relative):
    phase=Path(phase);rel=Path(relative)
    if rel.is_absolute() or '..' in rel.parts or Path(job_id).name!=job_id:raise ValueError('Unsafe artifact identity')
    path=phase/job_id/relative
    if path.is_file():return path.read_bytes()
    standalone=phase/job_id/'evidence.tar.gz'
    if standalone.is_file():
        with tarfile.open(standalone) as archive:return archive.extractfile(relative).read()
    db=sqlite3.connect(f'file:{phase/"completed-index.sqlite3"}?mode=ro',uri=True)
    try:row=db.execute('SELECT archive FROM jobs WHERE job=?',(job_id,)).fetchone()
    finally:db.close()
    if not row:raise FileNotFoundError(job_id)
    with tarfile.open(phase/'completed-archives'/row[0]) as archive:
        try:return archive.extractfile(job_id+'/'+relative).read()
        except KeyError:
            # Early v04 jobs were already independently compressed without loss.
            nested=archive.extractfile(job_id+'/evidence.tar.gz').read()
    with tarfile.open(fileobj=io.BytesIO(nested)) as archive:return archive.extractfile(relative).read()

def archive_batch(phase,batch_size=100):
    phase=Path(phase)
    if not phase.name.startswith(('formal-dojo-','formal-t2-')):raise ValueError('Only completed Dojo/T2 campaigns supported')
    target=phase/'completed-archives';target.mkdir(exist_ok=True)
    db=sqlite3.connect(phase/'completed-index.sqlite3',timeout=60)
    db.execute('CREATE TABLE IF NOT EXISTS archives (name TEXT PRIMARY KEY,sha256 TEXT,job_count INTEGER,created REAL)')
    db.execute('CREATE TABLE IF NOT EXISTS jobs (job TEXT PRIMARY KEY,archive TEXT,result TEXT,manifest TEXT,status TEXT)');db.commit()
    acquired=[];items=[];snapshot={}
    try:
        for path in sorted(phase.glob('*/result.json')):
            if len(items)>=batch_size:break
            job=path.parent
            if db.execute('SELECT 1 FROM jobs WHERE job=?',(job.name,)).fetchone():continue
            lock=(job/'job.lock').open('a')
            try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:lock.close();continue
            acquired.append(lock);result=json.loads(path.read_text())
            if result.get('status') not in TERMINAL:continue
            files=[]
            for f in sorted(job.rglob('*')):
                if f.is_symlink():raise ValueError('Unexpected evidence symlink')
                if f.is_file() and f.name!='job.lock':files.append(f)
            snapshot.update({str(f.relative_to(phase)):sha(f) for f in files})
            items.append((job,files,result))
        if not items:return 0
        name='batch-'+uuid.uuid4().hex+'.tar.gz';temporary=target/(name+'.part');final=target/name
        with tarfile.open(temporary,'w:gz') as archive:
            for _,files,_ in items:
                for f in files:archive.add(f,arcname=str(f.relative_to(phase)),recursive=False)
        with tarfile.open(temporary) as archive:
            for member,digest in snapshot.items():
                stream=archive.extractfile(member)
                if stream is None or hashlib.file_digest(stream,'sha256').hexdigest()!=digest:raise ValueError('Archive verification failed')
        with temporary.open('rb') as f:os.fsync(f.fileno())
        temporary.rename(final)
        for member,digest in snapshot.items():
            if sha(phase/member)!=digest:raise ValueError('Completed artifact changed while archiving')
        with db:
            db.execute('INSERT INTO archives VALUES (?,?,?,?)',(name,sha(final),len(items),time.time()))
            for job,_,result in items:
                read=lambda n:(job/n).read_text() if (job/n).exists() else '{}'
                db.execute('INSERT INTO jobs VALUES (?,?,?,?,?)',(job.name,name,json.dumps(result),read('manifest.json'),read('status.json')))
        # Commit the queryable result index before removing any verified source.
        for job,_,_ in items:shutil.rmtree(job)
        return len(items)
    finally:
        for lock in acquired:lock.close()
        db.close()

def main():
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--phase',required=True);p.add_argument('--watch',action='store_true');a=p.parse_args()
    if Path(a.phase).name!=a.phase:raise ValueError('Invalid phase')
    phase=a.root.resolve()/'runs'/a.phase
    with (phase/'archive.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        while True:
            count=archive_batch(phase);print(json.dumps({'archived_jobs':count,'phase':a.phase,'time':time.time()}),flush=True)
            if count==0:
                if not a.watch:return
                time.sleep(30)

if __name__=='__main__':main()
