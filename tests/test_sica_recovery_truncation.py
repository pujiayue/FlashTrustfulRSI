import json,sqlite3,tempfile,unittest
from collections import Counter
from pathlib import Path
from flashtrustfulrsi.sica_deepseek_twins import terminal_truncation

class TruncationRecoveryTests(unittest.TestCase):
    def test_only_last_settled_length_response_is_eligible(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'ledger.db'
            with sqlite3.connect(path) as db:
                db.execute('create table calls (call_id text, response text)')
                db.execute('insert into calls values (?,?)',('last',json.dumps({'model':'deepseek-flash','choices':[{'finish_reason':'length'}]})))
            status={'cause':'TransportError: Invalid or incomplete DeepSeek response'}
            ledger={'calls':[{'state':'success','call_id':'first'},{'state':'invalid_response','call_id':'last'}]}
            self.assertTrue(terminal_truncation(status,Counter(success=1,invalid_response=1),ledger,path))
            self.assertFalse(terminal_truncation(status,Counter(success=1,invalid_response=1,pending=1),ledger,path))
            self.assertFalse(terminal_truncation({'cause':'TransportError: other'},Counter(success=1,invalid_response=1),ledger,path))
            with sqlite3.connect(path) as db:db.execute('update calls set response=?',(json.dumps({'model':'deepseek-flash','choices':[{'finish_reason':'stop'}]}),))
            self.assertFalse(terminal_truncation(status,Counter(success=1,invalid_response=1),ledger,path))
