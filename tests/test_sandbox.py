import subprocess
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flashtrustfulrsi.sandbox import DockerSandbox, SandboxUnavailable


class SandboxTests(unittest.TestCase):
    def test_semantic_runtime_keeps_isolation_with_explicit_memory(self):
        sandbox=DockerSandbox(image='sha256:'+'a'*64,memory_mib=2048,tmpfs_mib=256)
        argv=sandbox.command(['true'],name='ftrsi-semantic')
        self.assertIn('--memory=2048m',argv);self.assertIn('--network=none',argv)
        self.assertIn('--tmpfs=/tmp:rw,noexec,nosuid,size=256m',argv)
        with self.assertRaises(ValueError):DockerSandbox(memory_mib=0)
    def setUp(self):
        self.sandbox = DockerSandbox(image="sha256:" + "a" * 64)

    def test_immutable_image_and_project_name(self):
        with self.assertRaises(ValueError): DockerSandbox(image="python:latest").command(["true"], name="ftrsi-test")
        with self.assertRaises(ValueError): self.sandbox.command(["true"], name="unrelated")
        argv = self.sandbox.command(["python", "task.py"], name="ftrsi-test")
        for flag in ("--network=none", "--read-only", "--pids-limit=64", "--cap-drop=ALL", "--pull=never"):
            self.assertIn(flag, argv)

    @patch("flashtrustfulrsi.sandbox.subprocess.run")
    def test_daemon_is_not_execution(self, run):
        run.return_value = subprocess.CompletedProcess([], 0, '{"ServerVersion":"29"}', "")
        report = DockerSandbox().probe()
        self.assertTrue(report["daemon_available"])
        self.assertFalse(report["execution_verified"])
        self.assertEqual(report["status"], "blocked")

    @patch("flashtrustfulrsi.sandbox.subprocess.run")
    def test_timeout_removes_verified_id_only(self, run):
        def execution(argv, timeout):
            Path(argv[argv.index('--cidfile')+1]).write_text('b'*64)
            token=next(x.split('=',2)[2] for x in argv if x.startswith('--label=org.flashtrustfulrsi.invocation='))
            run.side_effect=[subprocess.CompletedProcess([],0,json.dumps({'org.flashtrustfulrsi.owner':'FlashTrustfulRSI','org.flashtrustfulrsi.invocation':token})),subprocess.CompletedProcess([],0)]
            raise SandboxUnavailable('deadline')
        with patch.object(self.sandbox,'_execute',side_effect=execution):
            with self.assertRaises(SandboxUnavailable):self.sandbox.run(['true'],name='ftrsi-test',timeout=1)
        self.assertEqual(run.call_args_list[-1].args[0],['docker','rm','-f','b'*64])

    @patch("flashtrustfulrsi.sandbox.subprocess.run")
    def test_other_container_never_removed(self, run):
        with tempfile.TemporaryDirectory() as temp:
            cid=Path(temp)/'cid';cid.write_text('c'*64)
            run.return_value=subprocess.CompletedProcess([],0,'{}','')
            with self.assertRaises(SandboxUnavailable):self.sandbox._cleanup(cid,'this-invocation')
        self.assertEqual(run.call_count,1)

    def test_output_bounded_before_host_memory_grows(self):
        with self.assertRaisesRegex(SandboxUnavailable,'output limit'):
            self.sandbox._execute([sys.executable,'-c','print("x"*100000)'],10,output_limit=1024)

    def test_runtime_deadline_stops_client(self):
        with self.assertRaisesRegex(SandboxUnavailable,'deadline'):
            self.sandbox._execute([sys.executable,'-c','import time; time.sleep(2)'],.05)

    @patch.object(DockerSandbox,"_execute")
    def test_failed_workload_never_runs_on_host(self, run):
        run.return_value = subprocess.CompletedProcess([], 1, "", "unshare: operation not permitted")
        with self.assertRaises(SandboxUnavailable): self.sandbox.run(["python", "task.py"], name="ftrsi-test")
        self.assertEqual(run.call_count, 1)


if __name__ == "__main__": unittest.main()
