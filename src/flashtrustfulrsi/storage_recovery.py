"""Explicit reviewed replacement of storage-interrupted, ambiguous requests.

Original pending rows remain immutable and count as uncertain physical attempts.
Only an enumerated request may receive one new attempt under a separate ledger.
This is not an automatic retry or a claim that the original request was unbilled.
"""
import argparse,importlib,json,sys,threading
from pathlib import Path
from . import native_transport
from .durable_transport import DurableProvider
from .recovery import RecoveredProvider,ledger_rows,sha
from .provider import write_json

class StorageRecoveredProvider(RecoveredProvider):
    def __init__(self,config,root,job_id,limits,review):
        self.config=dict(config);self.job_id=job_id;self.sequence=0;self._seq_lock=threading.Lock()
        self.review=review;self.parent={};self.physical=[];self.replayed=set()
        if review['job_id']!=job_id or str(Path(root).resolve())!=review['original_root']:raise ValueError('Storage review identity mismatch')
        for source in review['parents']:
            if sha(source['path'])!=source['sha256']:raise ValueError('Immutable parent changed')
            rows=ledger_rows(source['path'],job_id);self.physical.extend(rows)
            for row in rows:
                old=self.parent.get(row['call'])
                if old and old['hash']!=row['hash']:raise native_transport.ReplayConflict('Parent payload hashes disagree')
                self.parent[row['call']]=row
        failed={k:{'status':r['status'],'error':r['error'],'hash':r['hash']} for k,r in self.parent.items() if r['status']!='success'}
        if failed!=review['authorized_replacements']:raise ValueError('Exact replacement list required')
        pending=sorted(k for k,v in failed.items() if v['status']=='pending')
        if pending!=sorted(review['ambiguous_calls']):raise ValueError('Every pending request needs explicit review')
        if review['ambiguous_outcome']!='unrecoverable_response_possible_charge_retained':raise ValueError('Uncertain billing must be retained')
        if not review['no_active_original_processes'] or not review['response_receipts_absent']:raise ValueError('Unreconciled active/available response')
        self.current=DurableProvider(config,review['attempt_root'],job_id,limits=limits);self.db=self.current.db

def install(path):
    document=json.loads(Path(path).read_text())
    if document['adapter_sha256']!=sha(__file__) or document['durability_sha256']!=sha(Path(__file__).with_name('durable_transport.py')):raise ValueError('Storage recovery adapter changed')
    index={(r['original_root'],r['job_id']):r for r in document['providers']}
    def factory(config,root,job_id,limits=None):
        item=index.get((str(Path(root).resolve()),job_id))
        if item is None:raise ValueError('Provider outside storage recovery review')
        return StorageRecoveredProvider(config,root,job_id,limits or native_transport.DEFAULT_LIMITS,item)
    native_transport.RecordedNativeProvider=factory;return document

def main():
    p=argparse.ArgumentParser();p.add_argument('--review',required=True);p.add_argument('--module',required=True);a,extra=p.parse_known_args()
    if a.module not in {'dojo_formal','evopath_formal','cim_formal','cim_evaluation'}:raise ValueError('Unsupported storage-recovery module')
    doc=install(a.review);write_json(Path(doc['audit_dir'])/'launch.json',{'review_sha256':sha(a.review),'argv':extra,'module':a.module})
    sys.argv=[sys.argv[0],*extra];importlib.import_module('.'+a.module,__package__).main()

if __name__=='__main__':main()
