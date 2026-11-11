import unittest
from flashtrustfulrsi.cim_native_labels import aggregate

class NativeLabelTests(unittest.TestCase):
    def test_paraphrase_never_becomes_original_label(self):
        labels=[[{'share':['my policy'],'private':['other']} for _ in range(10)] for _ in range(3)]
        result,quality=aggregate(['our policy','other'],labels)
        self.assertAlmostEqual(result['our policy']['unlabeled'],1)
        self.assertAlmostEqual(result['other']['private'],1)
        self.assertFalse(quality['strict_wording_valid'])
        self.assertEqual(quality['trials_with_unmatched_statements'],30)
    def test_share_precedence_and_no_missing_trials(self):
        labels=[[{'share':['a'],'private':['a']} for _ in range(10)] for _ in range(3)]
        result,quality=aggregate(['a'],labels)
        self.assertAlmostEqual(result['a']['necessary'],1)
        self.assertTrue(quality['strict_wording_valid'])
        labels[0].pop()
        with self.assertRaises(ValueError):aggregate(['a'],labels)

if __name__=='__main__':unittest.main()
