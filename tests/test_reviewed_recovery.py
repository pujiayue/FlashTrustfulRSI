import hashlib,json,sqlite3,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from flashtrustfulrsi.transport import RecordedProvider,ReplayConflict,TransportError
from flashtrustfulrsi.reviewed_recovery import ReviewedProvider


class RecoveryTests(unittest.TestCase):
    @patch('urllib.request.urlopen')
    def test_success_replayed_failed_preserved_and_replacement_recorded(self,network):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);secret=root/'secret.json'
            secret.write_text(json.dumps({'api_key':'fixture','base_url':'https://invalid.example/v1'}))
            config={'model':'gpt-5.6-sol','secret_ref':str(secret),'base_url':'https://invalid.example/v1'}
            body={'model':'gpt-5.6-sol','max_tokens':32,'messages':[{'role':'user','content':'one'}]}
            second={**body,'messages':[{'role':'user','content':'two'}]}
            response={'model':'gpt-5.6-sol','choices':[{'finish_reason':'stop','message':{'content':'ok'}}],'usage':{'prompt_tokens':4,'completion_tokens':2}}
            network.return_value.__enter__.return_value.read.return_value=json.dumps(response).encode()
            old=RecordedProvider(config,root/'old','job')
            old.invoke('chat',body)
            network.side_effect=TimeoutError()
            with self.assertRaises(TransportError):old.invoke('chat',second)
            backup=root/'frozen.sqlite3'
            with old.connect() as src,sqlite3.connect(backup) as dst:src.backup(dst)
            with sqlite3.connect(backup) as db:
                call,status,error,digest=db.execute("SELECT call,status,error,hash FROM calls WHERE status!='success'").fetchone()
            review={'ledger':'frozen.sqlite3','ledger_sha256':hashlib.sha256(backup.read_bytes()).hexdigest(),
                'authorized_replacements':{call:{'status':status,'error':error,'hash':digest}}}
            network.side_effect=None
            new=ReviewedProvider(config,root/'new','job',{'requests':3,'input_tokens':200000,'output_tokens':1000},review,root)
            self.assertEqual(new.invoke('chat',body),response)
            self.assertEqual(network.call_count,2)
            self.assertEqual(new.invoke('chat',second),response)
            self.assertEqual(network.call_count,3)
            self.assertEqual(new.summary()['logical_states'],{'success':2})
            self.assertEqual(new.summary()['physical_requests'],3)
            self.assertTrue(new.summary()['parent_timeout_billing_unknown'])
            self.assertEqual(hashlib.sha256(backup.read_bytes()).hexdigest(),review['ledger_sha256'])
            changed=ReviewedProvider(config,root/'other','job',{'requests':3,'input_tokens':200000,'output_tokens':1000},review,root)
            with self.assertRaises(ReplayConflict):changed.invoke('chat',second)
            with self.assertRaises(ReplayConflict):ReviewedProvider(config,root/'bad','job',{'requests':3,'input_tokens':200000,'output_tokens':1000},{**review,'authorized_replacements':{}},root)


if __name__=='__main__':unittest.main()
