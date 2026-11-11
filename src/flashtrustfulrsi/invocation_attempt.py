"""Explicit new identity for an interrupted, non-replayable tool invocation.

Never changes a saved request's hash. An entire incomplete invocation receives
a new namespace; all previous attempts remain in physical usage accounting.
"""
import json
from pathlib import Path
from . import native_spool

def install(path):
    review=json.loads(Path(path).read_text());mapping=review['prefixes']
    if not mapping or any(a==b or b in mapping for a,b in mapping.items()):raise ValueError('Distinct invocation attempt namespaces required')
    if review['old_attempt_status']!='interrupted_nonreplayable' or not review['preserve_old_calls']:raise ValueError('Incomplete invocation review required')
    original=native_spool.NativeSpoolProvider.invoke
    def invoke(self,protocol,body,request_id=None):
        if self.job_id==review['job_id'] and request_id:
            prefix,number=request_id.rsplit('-',1)
            if prefix in mapping:request_id=mapping[prefix]+'-'+number
        return original(self,protocol,body,request_id=request_id)
    native_spool.NativeSpoolProvider.invoke=invoke
