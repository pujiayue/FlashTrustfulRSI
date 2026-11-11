import unittest

from flashtrustfulrsi.sica_wire import reconcile_overseer_notifications


class OverseerWireTests(unittest.TestCase):
    def setUp(self):
        self.call = {'id':'committee-1','type':'function',
                     'function':{'name':'meta_improvement_review_committee','arguments':'{}'}}
        self.assistant = {'role':'assistant','content':'Proposal','tool_calls':[self.call]}
        self.notice = {'role':'user','content':'\n<OVERSEER_NOTIFICATION - 12:00:00>\nReview it.'}
        self.result = {'role':'tool','tool_call_id':'committee-1','content':'Original committee result'}

    def test_valid_messages_pass_through(self):
        history = [self.assistant,self.result,self.notice]
        self.assertEqual(reconcile_overseer_notifications(history),(history,[]))

    def test_delivered_result_is_moved_before_notification(self):
        fixed, edits = reconcile_overseer_notifications([self.assistant,self.notice,self.result])
        self.assertEqual(fixed,[self.assistant,self.result,self.notice])
        self.assertEqual(edits[0]['observed_result_ids'],['committee-1'])

    def test_pending_call_is_reported_without_fake_result(self):
        fixed, edits = reconcile_overseer_notifications([self.assistant,self.notice])
        self.assertNotIn('tool_calls',fixed[0])
        self.assertIn('No tool result was available',fixed[0]['content'])
        self.assertEqual(fixed[1],self.notice)
        self.assertEqual(edits[0]['pending_call_ids'],['committee-1'])
        self.assertEqual(self.assistant['tool_calls'],[self.call])


if __name__ == '__main__':
    unittest.main()
