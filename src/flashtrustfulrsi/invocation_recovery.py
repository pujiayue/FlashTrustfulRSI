"""Restore explicitly reviewed Responses session keys on interrupted invocations.

MiniAgent generates a UUID cache key on every process start. Restoring its prior
key preserves exact wire replay. No prompt, sampling or tool change is allowed.
"""
import hashlib,json
from pathlib import Path
from . import native_spool


def install(path):
    document=json.loads(Path(path).read_text());entries={}
    if document['field']!='prompt_cache_key':raise ValueError('Only cache session identity may be restored')
    for row in document['invocations']:
        source=Path(row['request_path']);raw=source.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=row['request_file_sha256']:raise ValueError('Original request changed')
        request=json.loads(raw)
        if request['job_id']!=document['job_id'] or request['protocol']!='responses':raise ValueError('Invalid reviewed identity/protocol')
        prefix=request['call_id'].rsplit('-',1)[0]
        if prefix in entries:raise ValueError('Duplicate invocation')
        if not isinstance(request['body'].get('prompt_cache_key'),str):raise ValueError('Missing original cache identity')
        entries[prefix]=request
    original=native_spool.NativeSpoolProvider.invoke
    def invoke(self,protocol,body,request_id=None):
        prefix=request_id.rsplit('-',1)[0] if request_id else ''
        if self.job_id==document['job_id'] and prefix in entries:
            saved=entries[prefix]
            if protocol!='responses':
                if request_id==saved['call_id']:raise ValueError('Reviewed request changed protocol')
                # Later native tool-loop requests can use Chat. The spool still
                # checks their full identity and never rewrites their payload.
                return original(self,protocol,body,request_id=request_id)
            body={**body,'prompt_cache_key':saved['body']['prompt_cache_key']}
            if request_id==saved['call_id'] and body!=saved['body']:raise ValueError('Recovered request differs beyond session cache key')
        return original(self,protocol,body,request_id=request_id)
    native_spool.NativeSpoolProvider.invoke=invoke
    return document
