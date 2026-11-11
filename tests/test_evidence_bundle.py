import json
from pathlib import Path
import tempfile
import unittest
from flashtrustfulrsi.evidence_bundle import collect

class EvidenceTests(unittest.TestCase):
    def test_negative_execution_is_not_promoted_and_artifacts_are_bound(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'dev-dsflash-negative-01';p.mkdir()
            (p/'manifest.json').write_text(json.dumps({'runs_formal_eval':False,'model':'deepseek-flash'}))
            (p/'status.json').write_text('{"status":"completed_with_execution_failures"}')
            (p/'result.json').write_text('{"safety":null}')
            a=collect(p,method='native',dimensions=['D2'],surfaces=['Workflow'],stages=['S4'])
            self.assertEqual(a['native_status']['status'],'completed_with_execution_failures')
            self.assertEqual(a['receipt_states'],{})
            self.assertEqual(a['native_result'],'result.json')
            self.assertTrue(all(len(row['sha256'])==64 for row in a['artifacts']))
            (p/'manifest.json').write_text('{"runs_formal_eval":true,"model":"deepseek-flash"}')
            with self.assertRaises(ValueError):collect(p,method='native',dimensions=['D2'],surfaces=['Workflow'],stages=['S4'])
