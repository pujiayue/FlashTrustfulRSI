import hashlib,json,sqlite3,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from flashtrustfulrsi.native_transport import RecordedNativeProvider,ReplayConflict,TransportError
from flashtrustfulrsi.recovery import RecoveredProvider

class RecoveryTests(unittest.TestCase):
    def test_exact_replay_and_one_reviewed_retry_preserve_physical_failures(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d).resolve();secret=root/'secret.json';secret.write_text(json.dumps({'base_url':'https://fixture.invalid/v1','api_key':'fixture'}))
            config={'model':'gpt-5.6-sol','base_url':'https://fixture.invalid/v1','secret_ref':str(secret)}
            limits={'requests':10,'input_tokens':1_000_000,'output_tokens':10000}
            body={'model':'gpt-5.6-sol','messages':[{'role':'user','content':'fixture'}],'max_tokens':50}
            response={'model':'gpt-5.6-sol','choices':[{'finish_reason':'stop','message':{'content':'fixture answer'}}],'usage':{'prompt_tokens':5,'completion_tokens':2}}
            old=RecordedNativeProvider(config,root/'old','job',limits)
            with patch('urllib.request.urlopen') as network:
                network.return_value.__enter__.return_value.read.return_value=json.dumps(response).encode();old.invoke('chat',body,request_id='one')
            with patch('urllib.request.urlopen',side_effect=TimeoutError):
                with self.assertRaises(TransportError):old.invoke('chat',body,request_id='two')
            backup=root/'parent.sqlite3'
            with sqlite3.connect(old.db) as src,sqlite3.connect(backup) as dst:src.backup(dst)
            with sqlite3.connect(backup) as db:failure=db.execute("select status,error,hash from calls where call='two'").fetchone()
            review={'review_id':'fixture-review','job_id':'job','original_root':str(root/'old'),'attempt_root':str(root/'new'),
                'parents':[{'path':str(backup),'sha256':hashlib.sha256(backup.read_bytes()).hexdigest()}],
                'authorized_replacements':{'two':dict(zip(('status','error','hash'),failure))}}
            recovered=RecoveredProvider(config,root/'old','job',limits,review)
            with patch('urllib.request.urlopen') as network:
                network.return_value.__enter__.return_value.read.return_value=json.dumps(response).encode()
                self.assertEqual(recovered.invoke('chat',body,request_id='one'),response)
                self.assertEqual(network.call_count,0)
                recovered.invoke('chat',body,request_id='two');recovered.invoke('chat',body,request_id='two')
                self.assertEqual(network.call_count,1)
                with self.assertRaises(ReplayConflict):recovered.invoke('chat',{**body,'max_tokens':49},request_id='one')
            summary=recovered.summary();self.assertEqual(summary['states'],{'success':2});self.assertEqual(summary['physical_states'],{'success':2,'failed':1})
            self.assertEqual(summary['requests'],3);self.assertEqual(summary['uncertain_requests'],1);self.assertEqual(summary['input_tokens'],10)

if __name__=='__main__':unittest.main()
