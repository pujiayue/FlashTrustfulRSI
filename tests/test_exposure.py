import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from flashtrustfulrsi.exposure import audit,clean_copy

class ExposureTests(unittest.TestCase):
    def test_failed_and_pending_requests_are_conservatively_excluded_without_rewriting_originals(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);data=root/'prepared';data.mkdir()
            rows=[{'id':'qa-id-alpha-unique','question':'Which alpha material is used?','answers':['a']},
                  {'id':'qa-id-beta-unique','question':'Which beta material is used?','answers':['b']}]
            raw=''.join(json.dumps(r)+'\n' for r in rows)
            for split in ('train','dev','test'):(data/(split+'.jsonl')).write_text(raw)
            folder=root/'runs/dev-dsflash-fixture/provider';folder.mkdir(parents=True)
            with sqlite3.connect(folder/'deepseek-debug.sqlite3') as db:
                db.execute('CREATE TABLE calls(call_id,request,status)')
                db.execute('INSERT INTO calls VALUES(?,?,?)',('one',json.dumps({'messages':[{'content':rows[0]['question']}]}),'pending'))
            report=audit(root,data);self.assertEqual(report['exposed_test_rows'],1)
            copy=clean_copy(report,root/'clean');self.assertEqual(copy['splits']['test']['rows'],1)
            self.assertEqual((data/'test.jsonl').read_text(),raw)
            remaining=json.loads((root/'clean/test.jsonl').read_text());self.assertEqual(remaining['id'],rows[1]['id'])
            with self.assertRaises(FileExistsError):clean_copy(report,root/'clean')
