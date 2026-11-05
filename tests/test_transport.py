import json, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
from flashtrustfulrsi.transport import RecordedProvider, MODEL, BudgetExceeded, ReplayConflict, AmbiguousRequest, TransportError

class TransportTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);secret=self.root/'credentials.json'
        secret.write_text(json.dumps({'base_url':'https://invalid.example/v1','api_key':'TEST_SECRET_NEVER_LOG'}))
        self.config={'model':MODEL,'base_url':'https://invalid.example/v1','secret_ref':str(secret)}
        self.p=RecordedProvider(self.config,self.root/'run','job')
        self.body={'model':MODEL,'messages':[{'role':'user','content':'test'}],'max_tokens':32}
        self.response={'model':MODEL,'choices':[{'finish_reason':'stop','message':{'content':'OK'}}],'usage':{'prompt_tokens':5,'completion_tokens':1}}
    def network(self,net):
        net.return_value.__enter__.return_value.read.return_value=json.dumps(self.response).encode()
    @patch('urllib.request.urlopen')
    def test_restart_replays_without_network(self,net):
        self.network(net);a=self.p.invoke('chat',self.body,'case')
        b=RecordedProvider(self.config,self.root/'run','job').invoke('chat',self.body,'case')
        self.assertEqual(a,b);self.assertEqual(net.call_count,1)
        self.assertEqual(self.p.summary()['requests'],1)
        for path in (self.root/'run/receipts').glob('*'):
            self.assertNotIn('TEST_SECRET_NEVER_LOG',path.read_text())
    @patch('urllib.request.urlopen')
    def test_identical_independent_trials_not_collapsed(self,net):
        self.network(net);self.p.invoke('chat',self.body,'trial1');self.p.invoke('chat',self.body,'trial2')
        self.assertEqual(net.call_count,2)
    @patch('urllib.request.urlopen')
    def test_changed_payload_requires_new_run(self,net):
        self.network(net);self.p.invoke('chat',self.body,'case')
        with self.assertRaises(ReplayConflict):self.p.invoke('chat',{**self.body,'max_tokens':33},'case')
        self.assertEqual(net.call_count,1)
    @patch('urllib.request.urlopen')
    def test_pending_never_retried(self,net):
        self.network(net);self.p.invoke('chat',self.body,'case')
        with self.p.connect() as db:db.execute("UPDATE calls SET status='pending'")
        with self.assertRaises(AmbiguousRequest):self.p.invoke('chat',self.body,'case')
        self.assertEqual(net.call_count,1)
    @patch('urllib.request.urlopen')
    def test_budget_survives_restart(self,net):
        self.network(net)
        limits={'requests':1,'input_tokens':100000,'output_tokens':1000}
        p=RecordedProvider(self.config,self.root/'limited','job',limits)
        p.invoke('chat',self.body,'first')
        p=RecordedProvider(self.config,self.root/'limited','job',limits)
        with self.assertRaises(BudgetExceeded):p.invoke('chat',self.body,'second')
        self.assertEqual(net.call_count,1)
    @patch('urllib.request.urlopen')
    def test_model_drift_fails_and_preserves_raw(self,net):
        self.response['model']='other';self.network(net)
        with self.assertRaises(TransportError):self.p.invoke('chat',self.body,'case')
        self.assertEqual(self.p.summary()['states'],{'failed':1})
        self.assertEqual(len(list((self.root/'run/receipts').glob('*.response.json'))),1)
    @patch('urllib.request.urlopen')
    def test_failed_never_automatically_retried(self,net):
        net.side_effect=TimeoutError()
        for _ in range(2):
            with self.assertRaises(TransportError):self.p.invoke('chat',self.body,'case')
        self.assertEqual(net.call_count,1)

if __name__=='__main__':unittest.main()
