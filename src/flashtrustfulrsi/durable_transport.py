"""Reserve response inodes and retain SQLite WAL files before network activity.

This operational guard changes no model payload or frozen scientific recipe.
It prevents inode exhaustion after a paid response from destroying its receipt.
"""
import argparse,hashlib,importlib,json,os,sqlite3,sys,time
from pathlib import Path
from . import native_transport

OriginalProvider=native_transport.RecordedNativeProvider

class DurableProvider(OriginalProvider):
    def invoke(self,protocol,body,request_id=None):
        if request_id is None:
            with self._seq_lock:self.sequence+=1;request_id=f'{self.sequence:08d}'
        read=sqlite3.connect(self.db,timeout=60)
        try:existing=read.execute('SELECT status FROM calls WHERE job=? AND call=?',(self.job_id,request_id)).fetchone()
        finally:read.close()
        if existing:return super().invoke(protocol,body,request_id=request_id)
        directory=self.root/'receipts';directory.mkdir(parents=True,exist_ok=True)
        key=hashlib.sha256((self.job_id+'\0'+request_id).encode()).hexdigest()
        reservations=[];anchor=sqlite3.connect(self.db,timeout=60)
        try:
            anchor.execute('PRAGMA journal_mode=WAL')
            # Keep the connection open across the HTTP request. Other connections
            # can commit without needing to recreate the WAL and SHM inodes.
            anchor.execute('SELECT count(*) FROM calls').fetchone()
            for kind in ('request','response'):
                path=directory/(key+'.'+kind+'.json.tmp')
                with path.open('xb') as f:os.fsync(f.fileno())
                reservations.append(path)
            return super().invoke(protocol,body,request_id=request_id)
        finally:
            anchor.close()
            for path in reservations:
                # Nonempty partial receipts are evidence and are never erased.
                if path.exists() and path.stat().st_size==0:path.unlink()

def install():
    native_transport.RecordedNativeProvider=DurableProvider
    # Recovery factories instantiate their current-attempt provider via this name.
    from . import recovery
    recovery.OriginalProvider=DurableProvider

def main():
    p=argparse.ArgumentParser();p.add_argument('--module',required=True);a,extra=p.parse_known_args()
    if a.module not in {'dojo_formal','evopath_formal','cim_formal','cim_evaluation','she_evolution','recovery','storage_recovery'}:raise ValueError('Unsupported module')
    if extra[:1]==['--']:extra=extra[1:]
    install();sys.argv=[sys.argv[0],*extra];importlib.import_module('.'+a.module,__package__).main()

if __name__=='__main__':main()
