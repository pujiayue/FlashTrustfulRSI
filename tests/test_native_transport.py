import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from flashtrustfulrsi.native_transport import RecordedNativeProvider,ReplayConflict,AmbiguousRequest


class NativeTransportTests(unittest.TestCase):
    @patch('urllib.request.urlopen')
    def test_native_cap_preserved_and_pending_never_resent(self,network):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);secret=root/'secret.json'
            secret.write_text(json.dumps({'api_key':'fixture-only','base_url':'https://invalid.example/v1'}))
            config={'model':'gpt-5.6-sol','secret_ref':str(secret),'base_url':'https://invalid.example/v1'}
            provider=RecordedNativeProvider(config,root/'run','native')
            body={'model':'gpt-5.6-sol','messages':[{'role':'user','content':'test'}],'max_completion_tokens':32768}
            response={'model':'gpt-5.6-sol','choices':[{'finish_reason':'stop','message':{'content':'ok'}}],'usage':{'prompt_tokens':1,'completion_tokens':1}}
            network.return_value.__enter__.return_value.read.return_value=json.dumps(response).encode()
            provider.invoke('chat',body,'one')
            self.assertEqual(json.loads(network.call_args.args[0].data),body)
            self.assertEqual(RecordedNativeProvider(config,root/'run','native').invoke('chat',body,'one'),response)
            with self.assertRaises(ReplayConflict):provider.invoke('chat',{**body,'max_completion_tokens':4096},'one')
            with provider.connect() as db:db.execute("UPDATE calls SET status='pending'")
            with self.assertRaises(AmbiguousRequest):provider.invoke('chat',body,'one')
            self.assertEqual(network.call_count,1)

    def test_conflicting_or_excessive_caps_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            provider=RecordedNativeProvider({'model':'gpt-5.6-sol','base_url':'https://invalid.example/v1'},Path(d),'native')
            for options in ({'max_tokens':1,'max_completion_tokens':1},{'max_output_tokens':32769}):
                with self.assertRaises(ValueError):provider.invoke('chat',{'model':'gpt-5.6-sol',**options},'bad')
            self.assertEqual(provider.summary()['requests'],0)


if __name__=='__main__':unittest.main()
