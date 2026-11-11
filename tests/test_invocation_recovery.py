import hashlib,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from flashtrustfulrsi import native_spool,invocation_recovery

class SessionRecovery(unittest.TestCase):
 def test_new_invocation_attempt_retains_wire_payload(self):
  from flashtrustfulrsi.invocation_attempt import install
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);review=root/'review.json';review.write_text(json.dumps({'job_id':'job','prefixes':{'old':'fresh'},'old_attempt_status':'interrupted_nonreplayable','preserve_old_calls':True}))
   seen=[]
   def original(self,protocol,body,request_id=None):seen.append((body,request_id))
   with patch.object(native_spool.NativeSpoolProvider,'invoke',original):
    install(review);provider=native_spool.NativeSpoolProvider(root,'job');body={'input':'unchanged'}
    provider.invoke('responses',body,request_id='old-0001');provider.invoke('responses',body,request_id='unrelated-0001')
    self.assertEqual(seen,[(body,'fresh-0001'),(body,'unrelated-0001')])
 def test_restore_only_session_and_reject_changed_prompt(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);body={'model':'gpt-5.6-sol','input':'unchanged','max_output_tokens':20,'prompt_cache_key':'original'}
   request={'job_id':'test','protocol':'responses','call_id':'target-000029-0001','body':body};source=root/'request.json';source.write_text(json.dumps(request))
   review=root/'review.json';review.write_text(json.dumps({'job_id':'test','field':'prompt_cache_key','invocations':[{'request_path':str(source),'request_file_sha256':hashlib.sha256(source.read_bytes()).hexdigest()}]}))
   seen=[]
   def original(self,protocol,body,request_id=None):seen.append(body);return 'ok'
   with patch.object(native_spool.NativeSpoolProvider,'invoke',original):
    invocation_recovery.install(review);provider=native_spool.NativeSpoolProvider(root,'test')
    self.assertEqual(provider.invoke('responses',{**body,'prompt_cache_key':'random'},request_id=request['call_id']),'ok')
    self.assertEqual(seen,[body])
    with self.assertRaises(ValueError):provider.invoke('responses',{**body,'input':'changed'},request_id=request['call_id'])
    provider.invoke('responses',{**body,'input':'next','prompt_cache_key':'random'},request_id='target-000029-0002')
    self.assertEqual(seen[-1]['prompt_cache_key'],'original')
    later_chat={'model':'gpt-5.6-sol','messages':[],'max_tokens':20}
    provider.invoke('chat',later_chat,request_id='target-000029-0003')
    self.assertEqual(seen[-1],later_chat)
    with self.assertRaisesRegex(ValueError,'changed protocol'):
     provider.invoke('chat',later_chat,request_id=request['call_id'])
    provider.invoke('responses',{**body,'prompt_cache_key':'new-task'},request_id='target-000030-0001')
    self.assertEqual(seen[-1]['prompt_cache_key'],'new-task')

if __name__=='__main__':unittest.main()
