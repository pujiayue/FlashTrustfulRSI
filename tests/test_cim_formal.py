import unittest
from flashtrustfulrsi.cim_formal import mix_labels

class CIMLabelsTests(unittest.TestCase):
    def test_native_priority_and_mixture(self):
        labels=[[{'share':['a'],'private':['a','b']}]*10,[{'share':['b'],'private':['a']}]*10,[{'share':[],'private':[]}]*10]
        result=mix_labels(['a','b','c'],labels)
        self.assertAlmostEqual(result['a']['necessary'],.26)
        self.assertAlmostEqual(result['a']['private'],.64)
        self.assertAlmostEqual(result['b']['necessary'],.64)
        self.assertAlmostEqual(result['b']['private'],.26)
        self.assertAlmostEqual(result['c']['unlabeled'],1.)
    def test_missing_trials_and_changed_statement_rejected(self):
        with self.assertRaises(ValueError):mix_labels(['a'],[[{'share':[],'private':[]}]*9]*3)
        with self.assertRaises(ValueError):mix_labels(['a'],[[{'share':['invented'],'private':[]}]*10]*3)

if __name__=='__main__':unittest.main()
