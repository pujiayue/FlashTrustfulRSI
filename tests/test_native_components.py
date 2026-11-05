"""Optional source-mode tests. Fetch sources before running this suite."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from flashtrustfulrsi.components import evaluate

ROOT=Path(os.environ.get('FTRSI_SOURCE_ROOT',Path(__file__).resolve().parents[1]))


@unittest.skipUnless((ROOT/'external/E08/evaluation/verdict.py').exists(), 'run scripts/prepare_components.py first')
class NativeComponentsTests(unittest.TestCase):
    def fixture(self,name):
        return json.loads((ROOT/'examples/components'/(name+'.json')).read_text())

    def test_bootstrap_reproducible(self):
        data=self.fixture('agentbreeder-bootstrap')
        a=evaluate('agentbreeder-bootstrap',data,ROOT)['result'];b=evaluate('agentbreeder-bootstrap',data,ROOT)['result']
        self.assertEqual(a,b);self.assertLessEqual(a['ci_lower'],a['median']);self.assertLessEqual(a['median'],a['ci_upper'])

    def test_missing_surface_never_zero_filled(self):
        with self.assertRaises(ValueError):evaluate('seabench-monitor-metrics',{'labels':[0],'predictions':[0],'surfaces':['memory']},ROOT)
        result=evaluate('seabench-monitor-metrics',self.fixture('seabench-monitor-metrics'),ROOT)['result']
        self.assertEqual(result['overall']['fp'],1);self.assertEqual(result['overall']['fn'],1)

    def test_unverified_and_duplicate_memory_rejected(self):
        data=self.fixture('safecoevo-memory');data['memories'].append(data['memories'][0].copy())
        result=evaluate('safecoevo-memory',data,ROOT)['result']
        self.assertEqual([x['accepted'] for x in result['admission']],[True,False,False])
        self.assertEqual([x['memory_id'] for x in result['memories']],['m1'])

    def test_skill_compilation_preserves_source_and_rejects_duplicate(self):
        fixture=self.fixture('safeevolve-skills');fixture['bank_path']=str(ROOT/'examples/components/skill-bank.json')
        result=evaluate('safeevolve-skills',fixture,ROOT)['result']
        self.assertEqual(result['compile_summary']['runtime_skill_count'],1)
        self.assertEqual(result['runtime_bank']['source_sha256'],result['compile_summary']['source_sha256'])
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'bank.json';bank=json.loads(Path(fixture['bank_path']).read_text())
            bank['general_skills']*=2;path.write_text(json.dumps(bank));fixture['bank_path']=str(path)
            with self.assertRaises(ValueError):evaluate('safeevolve-skills',fixture,ROOT)

    def test_patch_is_classified_without_execution(self):
        data={'patch':'+value=eval(payload)\n+safe=ast.literal_eval(payload)\n-removed=eval(payload)'}
        result=evaluate('trusting-trust-static',data,ROOT)['result']
        self.assertEqual(result['raw_eval'],1);self.assertEqual(result['literal_eval'],1)

    def test_agentpoison_denominators(self):
        data=self.fixture('agentpoison-react-metrics')
        result=evaluate('agentpoison-react-metrics',data,ROOT)['result']
        self.assertEqual(result['retrieval_denominator'],2);self.assertEqual(result['conditional_action_denominator'],1)
        self.assertIn('Accuracy:  0.5',result['native_stdout'])
        for row in data['rows']:row['retrieval_success']=row['overall_retrieval']=0
        with self.assertRaises(ValueError):evaluate('agentpoison-react-metrics',data,ROOT)


if __name__=='__main__':unittest.main()
