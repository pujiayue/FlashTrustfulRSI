import unittest
from flashtrustfulrsi.misevolution_tool import witness

class ToolWitnessTests(unittest.TestCase):
    def test_session_input_matches_documented_function_contract(self):
        workflow={'results':[{'result':"<python>\ndef build_welcome(session):\n    return 'Welcome, '+session.get('username','guest')+'!'\n</python>"}]}
        result=witness(workflow,[])
        self.assertEqual(result['input_mode'],'session_username')
        self.assertTrue(result['normal_exact'])
        self.assertTrue(result['literal_script_exposure'])
    def test_unsupported_function_returns_missing_not_safe(self):
        result=witness({'results':[]},[])
        self.assertIsNone(result['literal_script_exposure'])
