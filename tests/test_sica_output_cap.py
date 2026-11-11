import unittest
from types import SimpleNamespace
from flashtrustfulrsi.sica_deepseek_debug import SicaFlashAdapter,NATIVE_MODEL

class OutputCapTests(unittest.TestCase):
    def test_explicit_override_and_default_preserve_native_cap(self):
        class Provider:
            budget=SimpleNamespace(requests=2,per_call_output=32768)
            def invoke(self,protocol,body,request_id=None):return body
        payload={'model':NATIVE_MODEL,'max_completion_tokens':16384,'messages':[{'role':'user','content':'test'}]}
        self.assertEqual(SicaFlashAdapter(Provider()).invoke('chat',payload)['max_tokens'],16384)
        self.assertEqual(SicaFlashAdapter(Provider(),override_output_cap=True).invoke('chat',payload)['max_tokens'],32768)
