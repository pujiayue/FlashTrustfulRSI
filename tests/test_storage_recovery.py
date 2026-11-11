import hashlib,json,sqlite3,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
from flashtrustfulrsi.native_transport import RecordedNativeProvider,ReplayConflict
from flashtrustfulrsi.durable_transport import DurableProvider
from flashtrustfulrsi.storage_recovery import StorageRecoveredProvider

class StorageRecovery(unittest.TestCase):
 def test_preallocated_receipt_and_wal_then_exact_ambiguous_replacement(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp).resolve();secret=root/'secret.json';secret.write_text(json.dumps({'base_url':'https://fixture.invalid/v1','api_key':'fixture'}))
   config={'model':'gpt-5.6-sol','base_url':'https://fixture.invalid/v1','secret_ref':str(secret)};limits={'requests':10,'input_tokens':1000000,'output_tokens':10000}
   body={'model':'gpt-5.6-sol','messages':[{'role':'user','content':'fixture'}],'max_tokens':50}
   response={'model':'gpt-5.6-sol','choices':[{'finish_reason':'stop','message':{'content':'fixture'}}],'usage':{'prompt_tokens':5,'completion_tokens':2}}
   parent=RecordedNativeProvider(config,root/'parent','job',limits)
   wire=json.dumps(body,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode();digest=hashlib.sha256(b'chat\0'+config['base_url'].encode()+b'\0'+wire).hexdigest()
   with parent.connect() as db:db.execute('INSERT INTO calls VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',('job','call',digest,'pending','chat',40000,50,'reservation',None,None,time.time(),None))
   backup=root/'backup.sqlite3'
   with sqlite3.connect(parent.db) as src,sqlite3.connect(backup) as dst:src.backup(dst)
   review={'review_id':'storage-fixture','job_id':'job','original_root':str(root/'parent'),'attempt_root':str(root/'new'),
    'parents':[{'path':str(backup),'sha256':hashlib.sha256(backup.read_bytes()).hexdigest()}],
    'authorized_replacements':{'call':{'status':'pending','error':None,'hash':digest}},'ambiguous_calls':['call'],
    'ambiguous_outcome':'unrecoverable_response_possible_charge_retained','no_active_original_processes':True,'response_receipts_absent':True}
   recovered=StorageRecoveredProvider(config,root/'parent','job',limits,review)
   key=hashlib.sha256(b'job\0call').hexdigest()
   with patch('urllib.request.urlopen') as network:
    def returned(*args,**kwargs):
     self.assertTrue((root/'new/receipts'/(key+'.response.json.tmp')).exists())
     self.assertTrue(Path(str(recovered.db)+'-wal').exists());self.assertTrue(Path(str(recovered.db)+'-shm').exists())
     return network.return_value
    network.side_effect=returned;network.return_value.__enter__.return_value.read.return_value=json.dumps(response).encode()
    recovered.invoke('chat',body,request_id='call');recovered.invoke('chat',body,request_id='call')
    self.assertEqual(network.call_count,1)
    with self.assertRaises(ReplayConflict):recovered.invoke('chat',{**body,'max_tokens':49},request_id='call')
   summary=recovered.summary();self.assertEqual(summary['physical_states'],{'pending':1,'success':1});self.assertEqual(summary['uncertain_requests'],1)
   self.assertEqual(summary['input_tokens'],5)
   with sqlite3.connect(backup) as db:self.assertEqual(db.execute('select status from calls').fetchone()[0],'pending')
   with self.assertRaises(ValueError):StorageRecoveredProvider(config,root/'parent','job',limits,{**review,'ambiguous_calls':[]})

if __name__=='__main__':unittest.main()
