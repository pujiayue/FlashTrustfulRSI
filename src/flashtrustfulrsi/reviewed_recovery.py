"""Explicitly reviewed recovery: replay successes; preserve every failed attempt.

No failed/ambiguous call is resent unless its exact prior state is in the frozen
review document. A transport timeout remains unknown billing, never a zero-cost
or successful request. Subsequent failures stop again and need a new review.
"""
import hashlib,json,sqlite3
from pathlib import Path
from .native_transport import RecordedNativeProvider,TransportError as NativeError
from .transport import ReplayConflict,TransportError


class ReviewedProvider:
    def __init__(self,config,root,job_id,limits,review=None,project_root=None):
        self.config=dict(config);self.sequence=0;self.replayed=set();self.parent={};self.review=review
        self.current=RecordedNativeProvider({**config,'http_timeout_seconds':600},root,job_id,limits=limits)
        self.parent_usage={'requests':0,'states':{},'reported_input_tokens':0,'reported_output_tokens':0,'uncertain_requests':0}
        if review:
            path=Path(project_root)/review['ledger']
            if hashlib.sha256(path.read_bytes()).hexdigest()!=review['ledger_sha256']:raise ReplayConflict('Reviewed ledger changed')
            connection=sqlite3.connect(f'file:{path}?mode=ro',uri=True)
            try:
                connection.row_factory=sqlite3.Row
                for row in connection.execute('SELECT * FROM calls WHERE job=?',(job_id,)):
                    record=dict(row);self.parent[record['call']]=record
                    self.parent_usage['requests']+=1
                    states=self.parent_usage['states'];states[record['status']]=states.get(record['status'],0)+1
                    if record['usage_source']=='reported':
                        self.parent_usage['reported_input_tokens']+=record['input_tokens'];self.parent_usage['reported_output_tokens']+=record['output_tokens']
                    else:self.parent_usage['uncertain_requests']+=1
            finally:connection.close()
            failed={k:{'status':r['status'],'error':r['error'],'hash':r['hash']} for k,r in self.parent.items() if r['status']!='success'}
            if failed!=review['authorized_replacements']:raise ReplayConflict('Review does not exactly match unsuccessful calls')

    def invoke(self,protocol,body,request_id=None):
        if request_id is not None:raise ValueError('Native serial stream owns logical call order')
        self.sequence+=1;call=f'{self.sequence:08d}'
        data=json.dumps(body,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()
        digest=hashlib.sha256(protocol.encode()+b'\0'+self.config['base_url'].encode()+b'\0'+data).hexdigest()
        previous=self.parent.get(call)
        if previous:
            if previous['hash']!=digest:raise ReplayConflict('Recovered logical call changed payload')
            if previous['status']=='success':
                self.replayed.add(call);return json.loads(previous['response'])
            if call not in self.review['authorized_replacements']:raise TransportError('No explicit recovery review for failed call')
        try:return self.current.invoke(protocol,body,request_id=call)
        except NativeError as exc:raise TransportError(str(exc)) from exc

    def summary(self):
        current=self.current.summary();states=dict(current['states'])
        states['success']=states.get('success',0)+len(self.replayed)
        return {'model':self.config['model'],'logical_states':states,'replayed_successful_calls':len(self.replayed),
            'current_attempt':current,'parent_attempt':self.parent_usage,
            'physical_requests':self.parent_usage['requests']+current['requests'],
            'parent_timeout_billing_unknown':bool(self.parent_usage['uncertain_requests'])}
