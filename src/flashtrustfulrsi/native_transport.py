"""Versioned transport for native SDK protocols and up to 32768 output tokens.

The earlier transport module remains frozen for already-running campaigns.

Cache identity is (job_id, call_id), never prompt alone: independent trials stay
independent. A pending/ambiguous request is never silently sent a second time.
"""
import asyncio, hashlib, json, os, sqlite3, threading, time, urllib.request, urllib.error
from contextlib import contextmanager
from pathlib import Path
from .provider import write_json

MODEL='gpt-5.6-sol'
DEFAULT_LIMITS={'requests':300,'input_tokens':10_000_000,'output_tokens':1_000_000}
class TransportError(RuntimeError): pass
class BudgetExceeded(TransportError): pass
class ReplayConflict(TransportError): pass
class AmbiguousRequest(TransportError): pass

class RecordedNativeProvider:
    def __init__(self,config,root,job_id,limits=None):
        if config.get('model')!=MODEL:raise ValueError('This campaign requires exact model '+MODEL)
        if not job_id or len(job_id)>200:raise ValueError('Explicit stable job_id required')
        self.config=dict(config);self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True)
        self.job_id=job_id;self.sequence=0;self._seq_lock=threading.Lock()
        self.db=self.root/'ledger.sqlite3';limits=limits or DEFAULT_LIMITS
        if set(limits)!=set(DEFAULT_LIMITS) or any(type(v)!=int or v<=0 for v in limits.values()):raise ValueError('Invalid phase limits')
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS calls (job TEXT, call TEXT, hash TEXT, status TEXT, protocol TEXT, input_tokens INTEGER, output_tokens INTEGER, usage_source TEXT, response TEXT, error TEXT, started REAL, finished REAL, PRIMARY KEY(job,call))')
            stored=db.execute("SELECT value FROM config WHERE key='limits'").fetchone()
            if stored and json.loads(stored[0])!=limits:raise ValueError('Immutable campaign limits differ; do not reset old campaign')
            db.execute('INSERT OR IGNORE INTO config VALUES (?,?)',('limits',json.dumps(limits)))
    @contextmanager
    def connect(self):
        db=sqlite3.connect(self.db,timeout=60)
        try:
            db.execute('PRAGMA journal_mode=WAL');db.execute('PRAGMA busy_timeout=60000')
            with db:yield db
        finally:db.close()
    def __deepcopy__(self,memo):return self
    async def complete(self,messages,*,max_tokens=2048,request_id=None,**options):
        return await asyncio.to_thread(self.complete_sync,messages,max_tokens=max_tokens,request_id=request_id,**options)
    def complete_sync(self,messages,*,max_tokens=2048,request_id=None,**options):
        if set(options)-{'tools','tool_choice','response_format'}:raise ValueError('Unsupported provider option')
        body={'model':MODEL,'messages':messages,'max_tokens':max_tokens,**options}
        return self.invoke('chat',body,request_id=request_id)
    def invoke(self,protocol,body,request_id=None):
        suffix={'chat':'/chat/completions','responses':'/responses','anthropic':'/messages'}
        if protocol not in suffix or body.get('model')!=MODEL:raise ValueError('Invalid protocol/model')
        fields=[k for k in ('max_tokens','max_output_tokens','max_completion_tokens') if k in body]
        if len(fields)!=1:raise ValueError('Exactly one explicit native output token cap required')
        max_tokens=body[fields[0]]
        if type(max_tokens)!=int or not 1<=max_tokens<=32768:raise ValueError('Output cap must be 1..32768')
        if request_id is None:
            with self._seq_lock:self.sequence+=1;request_id=f'{self.sequence:08d}'
        if not isinstance(request_id,str) or not request_id:raise ValueError('Invalid request ID')
        data=json.dumps(body,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()
        digest=hashlib.sha256(protocol.encode()+b'\0'+self.config['base_url'].encode()+b'\0'+data).hexdigest()
        request_path=self.root/'receipts'/hashlib.sha256((self.job_id+'\0'+request_id).encode()).hexdigest()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old=db.execute('SELECT hash,status,response,error FROM calls WHERE job=? AND call=?',(self.job_id,request_id)).fetchone()
            if old:
                if old[0]!=digest:raise ReplayConflict('Same logical call has changed payload/protocol')
                if old[1]=='success':return json.loads(old[2])
                if old[1]=='pending':raise AmbiguousRequest('Pending request requires reconciliation; no resend')
                raise TransportError('Previously failed request: '+str(old[3]))
            limits=json.loads(db.execute("SELECT value FROM config WHERE key='limits'").fetchone()[0])
            used=db.execute('SELECT count(*),coalesce(sum(input_tokens),0),coalesce(sum(output_tokens),0) FROM calls').fetchone()
            # Provider has historically billed hidden input overhead. Keep a floor
            # and observed maximum; reservation is not a provider billing guarantee.
            observed=db.execute("SELECT coalesce(max(input_tokens),0) FROM calls WHERE usage_source='reported'").fetchone()[0]
            reserve=max(32768,observed)+len(data)
            for k,u,n in zip(DEFAULT_LIMITS,used,(1,reserve,max_tokens)):
                if u+n>limits[k]:raise BudgetExceeded(k)
            db.execute('INSERT INTO calls VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',(self.job_id,request_id,digest,'pending',protocol,reserve,max_tokens,'reservation',None,None,time.time(),None))
        write_json(request_path.with_suffix('.request.json'),{'job_id':self.job_id,'call_id':request_id,'protocol':protocol,'hash':digest,'body':body})
        status='failed';result=None;error=None;usage_source='reservation';inp=reserve;out=max_tokens
        try:
            creds=json.loads(Path(self.config['secret_ref']).read_text())
            if creds['base_url'].rstrip('/')!=self.config['base_url'].rstrip('/'):raise TransportError('Approved endpoint mismatch')
            headers={'Authorization':'Bearer '+creds['api_key'],'Content-Type':'application/json'}
            if protocol=='anthropic':headers.update({'x-api-key':creds['api_key'],'anthropic-version':'2023-06-01'})
            req=urllib.request.Request(self.config['base_url'].rstrip('/')+suffix[protocol],data=data,headers=headers)
            timeout=self.config.get('http_timeout_seconds',600)
            if type(timeout)!=int or not 1<=timeout<=600:raise ValueError('Invalid native transport timeout')
            with urllib.request.urlopen(req,timeout=timeout) as r:result=json.loads(r.read())
            write_json(request_path.with_suffix('.response.json'),result)
            usage=result.get('usage') or {};ik='prompt_tokens' if protocol=='chat' else 'input_tokens';ok='completion_tokens' if protocol=='chat' else 'output_tokens'
            if all(type(usage.get(k))==int and usage[k]>=0 for k in (ik,ok)):inp,out,usage_source=usage[ik],usage[ok],'reported'
            if result.get('model')!=MODEL:raise TransportError('Returned model differs from requested model')
            if protocol=='chat':
                choice=result['choices'][0]
                if choice.get('finish_reason') not in ('stop','tool_calls'):raise TransportError('Incomplete generation: '+str(choice.get('finish_reason')))
                if not(choice['message'].get('content') or choice['message'].get('tool_calls')):raise TransportError('Empty generation')
            elif protocol=='responses' and result.get('status')!='completed':raise TransportError('Responses did not complete')
            elif protocol=='anthropic' and result.get('stop_reason') not in ('end_turn','tool_use','stop_sequence'):raise TransportError('Messages did not complete')
            status='success'
            return result
        except urllib.error.HTTPError as e:
            error='HTTP '+str(e.code);raise TransportError(error+'; no automatic retry') from None
        except Exception as e:
            error=str(e) if isinstance(e,TransportError) else type(e).__name__
            raise TransportError(error) from None
        finally:
            with self.connect() as db:db.execute('UPDATE calls SET status=?,input_tokens=?,output_tokens=?,usage_source=?,response=?,error=?,finished=? WHERE job=? AND call=?',(status,inp,out,usage_source,json.dumps(result) if result is not None else None,error,time.time(),self.job_id,request_id))
    def summary(self):
        with self.connect() as db:
            used=db.execute('SELECT count(*),coalesce(sum(input_tokens),0),coalesce(sum(output_tokens),0) FROM calls').fetchone()
            states=dict(db.execute('SELECT status,count(*) FROM calls GROUP BY status'))
        return dict(zip(DEFAULT_LIMITS,used),states=states,model=MODEL)
