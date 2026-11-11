"""Audited attempt recovery without changing frozen scientific protocols.

Each parent ledger is an immutable SQLite backup. Reuse requires exact wire
payload identity. Only enumerated unsuccessful calls may receive one new
attempt. Logical outcomes and physical attempts/uncertain billing are separate.
"""
import argparse,hashlib,importlib,json,runpy,sqlite3,sys,threading,time
from collections import Counter
from pathlib import Path
from . import native_transport
from .provider import write_json

OriginalProvider=native_transport.RecordedNativeProvider


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def ledger_rows(path,job):
    with sqlite3.connect(f'file:{Path(path).resolve()}?mode=ro',uri=True) as db:
        db.row_factory=sqlite3.Row
        return [dict(r) for r in db.execute('SELECT * FROM calls WHERE job=?',(job,))]


class RecoveredProvider(OriginalProvider):
    def __init__(self,config,root,job_id,limits,review):
        self.config=dict(config);self.job_id=job_id;self.sequence=0;self._seq_lock=threading.Lock()
        self.review=review;self.parent={};self.physical=[];self.replayed=set()
        if review['job_id']!=job_id or str(Path(root).resolve())!=review['original_root']:raise ValueError('Recovery identity mismatch')
        for source in review['parents']:
            if sha(source['path'])!=source['sha256']:raise ValueError('Frozen parent ledger changed')
            rows=ledger_rows(source['path'],job_id);self.physical.extend(rows)
            for row in rows:
                old=self.parent.get(row['call'])
                if old and old['hash']!=row['hash']:raise native_transport.ReplayConflict('Parent call hashes disagree')
                self.parent[row['call']]=row
        failed={k:{'status':r['status'],'error':r['error'],'hash':r['hash']} for k,r in self.parent.items() if r['status']!='success'}
        if failed!=review['authorized_replacements']:raise ValueError('Exact failed-call review required')
        if any(v['status']=='pending' for v in failed.values()):raise ValueError('Pending calls require provider reconciliation first')
        self.current=OriginalProvider(config,review['attempt_root'],job_id,limits=limits)
        self.db=self.current.db

    def invoke(self,protocol,body,request_id=None):
        if request_id is None:
            with self._seq_lock:self.sequence+=1;request_id=f'{self.sequence:08d}'
        data=json.dumps(body,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()
        digest=hashlib.sha256(protocol.encode()+b'\0'+self.config['base_url'].encode()+b'\0'+data).hexdigest()
        row=self.parent.get(request_id)
        if row:
            if row['hash']!=digest:raise native_transport.ReplayConflict('Recovery payload differs: '+request_id)
            if row['status']=='success':self.replayed.add(request_id);return json.loads(row['response'])
            if request_id not in self.review['authorized_replacements']:raise ValueError('Unreviewed failed request')
        return self.current.invoke(protocol,body,request_id=request_id)

    def summary(self):
        current=ledger_rows(self.current.db,self.job_id);logical={**self.parent,**{r['call']:r for r in current}}
        physical=self.physical+current
        reported=[r for r in physical if r['usage_source']=='reported'];uncertain=[r for r in physical if r['usage_source']!='reported']
        return {'model':self.config['model'],'states':dict(Counter(r['status'] for r in logical.values())),
            'states_semantics':'logical_latest_attempt; physical_states below retain all failed attempts',
            'requests':len(physical),'input_tokens':sum(r['input_tokens'] for r in reported),'output_tokens':sum(r['output_tokens'] for r in reported),
            'usage_semantics':'reported tokens only; reservation is never actual usage',
            'physical_states':dict(Counter(r['status'] for r in physical)), 'logical_requests':len(logical),
            'uncertain_requests':len(uncertain),'uncertain_reserved_input_tokens':sum(r['input_tokens'] for r in uncertain),
            'uncertain_reserved_output_tokens':sum(r['output_tokens'] for r in uncertain),'replayed_successful_calls':len(self.replayed),
            'review_id':self.review['review_id']}


def install(review_path):
    document=json.loads(Path(review_path).read_text())
    if document['recovery_adapter_sha256']!=sha(__file__):raise ValueError('Recovery adapter changed')
    index={(r['original_root'],r['job_id']):r for r in document['providers']}
    def factory(config,root,job_id,limits=None):
        item=index.get((str(Path(root).resolve()),job_id))
        if item is None:raise ValueError('Provider outside recovery review')
        return RecoveredProvider(config,root,job_id,limits or native_transport.DEFAULT_LIMITS,item)
    native_transport.RecordedNativeProvider=factory
    return document


def fatal_container_transport():
    """Quota/transport failures must stop a run, not become model-quality zeros."""
    from . import native_container
    OriginalSocket=native_container.InferenceSocket
    class Socket(OriginalSocket):
        def __enter__(self):
            invoke=self.invoke;self.fatal=[]
            def checked(*args,**kwargs):
                try:return invoke(*args,**kwargs)
                except Exception as exc:self.fatal.append(exc);raise
            self.invoke=checked;return super().__enter__()
        def __exit__(self,*args):
            result=super().__exit__(*args)
            if self.fatal:raise RuntimeError('Inference transport failed; scientific run stopped') from self.fatal[0]
            return result
    native_container.InferenceSocket=Socket


def main():
    p=argparse.ArgumentParser();p.add_argument('--review',required=True);p.add_argument('--module',required=True)
    a,extra=p.parse_known_args()
    allowed={'dojo_formal','evopath_formal','cim_formal','she_evolution'}
    if a.module not in allowed:raise ValueError('Recovery module not allowed')
    document=install(a.review)
    write_json(Path(document['audit_dir'])/'launch.json',{'review_sha256':sha(a.review),'adapter_sha256':sha(__file__),
        'module':a.module,'argv':extra,'started':time.time()})
    sys.argv=[sys.argv[0],*extra];importlib.import_module('.'+a.module,__package__).main()

if __name__=='__main__':main()
