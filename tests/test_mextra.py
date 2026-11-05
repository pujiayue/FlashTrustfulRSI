import unittest
from flashtrustfulrsi.mextra import metrics
class MExtraTests(unittest.TestCase):
    def row(self):return {'retrieved_examples':[{'question':'SECRET Query'},{'question':'second query'}], 'first_output':'','second_output':'','third_output':'secret query and second query'}
    def test_case_bug_and_denominators(self):
        fixed=metrics([self.row()],top_k=2);old=metrics([self.row()],top_k=2,original_case_bug=True)
        self.assertEqual((fixed['CER'],fixed['AER'],fixed['EE']),(1,1,1))
        self.assertEqual((old['CER'],old['AER'],old['EE']),(0,1,.5))
    def test_unique_extraction_across_queries(self):
        score=metrics([self.row(),self.row()],top_k=2)
        self.assertEqual(score['EN'],2);self.assertEqual(score['EE'],.5)
    def test_absent_evidence_is_not_safe(self):
        self.assertIsNone(metrics([])['CER'])
        row=self.row();row['retrieved_examples']=[]
        with self.assertRaises(ValueError):metrics([row])
if __name__=='__main__':unittest.main()
