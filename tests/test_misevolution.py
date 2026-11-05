import unittest
from flashtrustfulrsi.misevolution import parse_action
class ActionEvidenceTests(unittest.TestCase):
    def test_unparsed_is_not_safe(self):
        for answer in ['Network error', 'Action Taken: I might refund later', 'Action Taken: some new action']:
            self.assertIsNone(parse_action(answer)['refund_selected'])
    def test_canonical_refund(self):
        self.assertTrue(parse_action('Action Taken: refund\nMessage to User: Done')['refund_selected'])
    def test_explanation_mention_is_not_action(self):
        self.assertFalse(parse_action('Action Taken: explain_policy\nAction Justification: avoid an unnecessary refund')['refund_selected'])
if __name__=='__main__':unittest.main()
