import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from flashtrustfulrsi.composition_recovery import prepare,restore_rows

@unittest.skipUnless(importlib.util.find_spec('flashrsi'),'optional native package')
class RecoveryTests(unittest.TestCase):
    def test_exact_json_task_serialization_restores_tuple_metadata_without_changing_labels(self):
        from flashrsi.core import Task,Trajectory
        from flashrsi._records import _trajectory_record
        task=Task('one','question',ground_truth='answer',metadata={'answers':('answer',)})
        value=json.loads(json.dumps(_trajectory_record(Trajectory(task=task,output='answer')),default=dict))
        self.assertEqual(restore_rows([value],[task])[0].task,task)
        value['task']['ground_truth']='wrong'
        with self.assertRaises(ValueError):restore_rows([value],[task])

    def test_pending_source_call_prevents_recovery_before_any_snapshot_read(self):
        from flashtrustfulrsi.deepseek_debug import DeepSeekProvider
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ,{'DEEPSEEK_API_KEY':'fixture'}):
            run=Path(temp);(run/'manifest.json').write_text('{"model":"deepseek-flash","runs_formal_eval":false}')
            (run/'status.json').write_text('{"status":"failed"}')
            provider=DeepSeekProvider(run/'provider','fixture')
            response={'id':'fixture','model':'deepseek-flash','choices':[{'message':{'content':'ok'},'finish_reason':'stop'}],
                      'usage':{'prompt_tokens':1,'completion_tokens':1}}
            with patch.object(provider,'_send',return_value=response):provider.complete_sync([{'role':'user','content':'probe'}],request_id='one')
            with sqlite3.connect(provider.db) as db:db.execute("UPDATE calls SET status='pending'")
            with self.assertRaisesRegex(ValueError,'Reconcile unresolved'):prepare(run)
