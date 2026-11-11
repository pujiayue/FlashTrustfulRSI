"""Credential-free request spool for a separately authorized model execution host.

Only the original model host runs RecordedProvider and stores the API credential.
Logical request IDs survive bridge/process restarts; no request is silently retried.
"""
import hashlib,json,re,time
from pathlib import Path
from .provider import write_json
from .transport import MODEL,ReplayConflict,TransportError

class NativeSpoolProvider:
    def __init__(self,root,job_id,timeout=900):
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',job_id):raise ValueError('Invalid job ID')
        self.root=Path(root)/job_id;self.root.mkdir(parents=True,exist_ok=True);self.job_id=job_id;self.timeout=timeout

    def complete_sync(self, messages, *, max_tokens, request_id, **options):
        body = {**options, 'model': MODEL, 'messages': messages, 'max_tokens': max_tokens}
        return self.invoke('chat', body, request_id=request_id)

    def invoke(self,protocol,body,request_id=None):
        if protocol not in ('chat','responses','anthropic') or body.get('model')!=MODEL or not request_id:raise ValueError('Explicit exact-model chat request required')
        fields=[k for k in ('max_tokens','max_output_tokens','max_completion_tokens') if k in body]
        if len(fields)!=1 or type(body[fields[0]])!=int or not 1<=body[fields[0]]<=32768:raise ValueError('Invalid native output cap')
        digest=hashlib.sha256(json.dumps(body,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
        identity=hashlib.sha256((self.job_id+'\0'+request_id).encode()).hexdigest()
        path=self.root/identity;request=path.with_suffix('.request.json')
        document={'job_id':self.job_id,'call_id':request_id,'protocol':protocol,'body_sha256':digest,'body':body}
        if request.exists() and json.loads(request.read_text())!=document:raise ReplayConflict('Spool payload changed')
        write_json(request,document);deadline=time.monotonic()+self.timeout
        while time.monotonic()<deadline:
            error=path.with_suffix('.error.json');response=path.with_suffix('.response.json')
            if error.exists():raise TransportError(json.loads(error.read_text()).get('error','Bridge error'))
            if response.exists():
                result=json.loads(response.read_text())
                if result['body_sha256']!=digest:raise ReplayConflict('Response hash mismatch')
                if result['response'].get('model')!=MODEL:raise TransportError('Returned model mismatch')
                return result['response']
            time.sleep(.5)
        raise TransportError('Spool wait deadline; request remains pending for reconciliation')
