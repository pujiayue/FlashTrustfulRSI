import hashlib,json,tempfile,unittest
from pathlib import Path
from flashtrustfulrsi.spool_provider import SpoolProvider
from flashtrustfulrsi.transport import MODEL,ReplayConflict,TransportError

class SpoolProviderTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.provider=SpoolProvider(Path(self.temp.name),'job',timeout=.01)
        self.body={'model':MODEL,'messages':[{'role':'user','content':'fixture'}],'max_tokens':32}
        self.name=hashlib.sha256(b'job\0case').hexdigest()

    def response(self,digest=None):
        digest=digest or hashlib.sha256(json.dumps(self.body,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
        response={'body_sha256':digest,'response':{'model':MODEL,'choices':[{'message':{'content':'fixture'}}]}}
        (self.provider.root/(self.name+'.response.json')).write_text(json.dumps(response));return response['response']

    def test_replay_and_payload_conflict(self):
        expected=self.response()
        self.assertEqual(self.provider.invoke('chat',self.body,'case'),expected)
        self.assertEqual(self.provider.invoke('chat',self.body,'case'),expected)
        with self.assertRaises(ReplayConflict):self.provider.invoke('chat',{**self.body,'max_tokens':33},'case')
        self.assertEqual(len(list(self.provider.root.glob('*.request.json'))),1)

    def test_response_must_match_request_hash(self):
        self.response('wrong')
        with self.assertRaises(ReplayConflict):self.provider.invoke('chat',self.body,'case')

    def test_timeout_preserves_pending_request(self):
        with self.assertRaises(TransportError):self.provider.invoke('chat',self.body,'case')
        self.assertTrue((self.provider.root/(self.name+'.request.json')).exists())

if __name__=='__main__':unittest.main()
