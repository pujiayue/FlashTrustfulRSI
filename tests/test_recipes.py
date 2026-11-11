import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from flashtrustfulrsi.recipes import SCHEMA,load_recipe,prepare,run_prepared
from flashtrustfulrsi.deepseek_debug import DeepSeekProvider

class RecipeTests(unittest.TestCase):
    def recipe(self,root,**changes):
        value={'schema':SCHEMA,'scope':'development_only','model':'deepseek-flash','formal_data_used':False,
               'profile':'misevolution-online','parameters':{'job_id':'dev-dsflash-misevolution-fixture'},'max_wall_seconds':30}
        value.update(changes);p=root/'recipe.json';p.write_text(json.dumps(value));return p

    def test_prepared_execution_is_idempotent_and_refuses_modified_inputs(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source=root/'input.txt';source.write_text('frozen')
            import hashlib
            recipe=self.recipe(root,input_sha256={'input.txt':hashlib.sha256(source.read_bytes()).hexdigest()})
            calls=[]
            def runner(argv):
                action=argv[argv.index('debug')+1];calls.append(action)
                if action=='plan':return SimpleNamespace(returncode=0,stdout='{"model":"deepseek-flash","runs_formal_eval":false}',stderr='')
                run=root/'runs/dev-dsflash-misevolution-fixture';run.mkdir(parents=True)
                (run/'manifest.json').write_text('{"model":"deepseek-flash","runs_formal_eval":false}')
                (run/'status.json').write_text('{"status":"completed"}')
                (run/'result.json').write_text('{"status":"completed"}')
                DeepSeekProvider(run/'provider','fixture') # Zero-call recorded fixture, not model evidence.
                return SimpleNamespace(returncode=0,stdout='',stderr='')
            prepared=root/'prepared.json';prepare(recipe,root,prepared,runner)
            self.assertTrue(run_prepared(prepared,runner)['accepted_development_completion'])
            again=run_prepared(prepared,runner)
            self.assertTrue(again['idempotent_read']);self.assertEqual(again['new_model_requests'],0)
            self.assertEqual(calls,['plan','plan','run'])
            source.write_text('changed')
            with self.assertRaisesRegex(ValueError,'input changed'):run_prepared(prepared,runner)
            self.assertEqual(calls,['plan','plan','run'])

    def test_no_model_overrides_or_formal_scope_or_existing_prepared_overwrite(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            for bad in ({'model':'gpt-5.6-sol'},{'formal_data_used':True},
                        {'parameters':{'job_id':'dev-dsflash-misevolution-fixture','provider':'old.json'}}):
                with self.assertRaises(ValueError):load_recipe(self.recipe(root,**bad))
            recipe=self.recipe(root);out=root/'prepared.json';out.write_text('existing')
            with self.assertRaises(FileExistsError):prepare(recipe,root,out)
            self.assertEqual(out.read_text(),'existing')

    def test_directory_inputs_bind_membership_and_contents(self):
        from flashtrustfulrsi.recipes import digest
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);data=root/'data';data.mkdir();(data/'manifest.json').write_text('{}')
            before=digest(data);(data/'added.json').write_text('{}')
            self.assertNotEqual(before,digest(data))
            (data/'link').symlink_to(data/'manifest.json')
            with self.assertRaisesRegex(ValueError,'symbolic link'):digest(data)

    def test_timeout_kills_only_this_invocation_group_and_commits_failed_receipt(self):
        import subprocess
        from unittest.mock import patch,Mock
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);recipe=self.recipe(root)
            plan=SimpleNamespace(returncode=0,stdout='{"model":"deepseek-flash","runs_formal_eval":false}',stderr='')
            prepared=root/'prepared.json';prepare(recipe,root,prepared,lambda argv:plan)
            process=Mock(pid=987654321);process.wait.side_effect=[subprocess.TimeoutExpired('fixture',30),-9]
            with patch('flashtrustfulrsi.recipes.protocol_plan',return_value=json.loads(plan.stdout)), \
                 patch('flashtrustfulrsi.recipes.subprocess.Popen',return_value=process) as start, \
                 patch('flashtrustfulrsi.recipes.os.killpg') as kill:
                receipt=run_prepared(prepared)
            self.assertTrue(start.call_args.kwargs['start_new_session']);kill.assert_called_once_with(process.pid,9)
            self.assertTrue(receipt['wrapper_timeout']);self.assertFalse(receipt['accepted_development_completion'])
            again=run_prepared(prepared);self.assertTrue(again['idempotent_read']);self.assertFalse(again['accepted_development_completion'])
