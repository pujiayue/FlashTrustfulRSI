import subprocess
import unittest
from unittest.mock import patch

from flashtrustfulrsi.sandbox import DockerSandbox, SandboxUnavailable


class SandboxTests(unittest.TestCase):
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
    def test_timeout_removes_only_own_container(self, run):
        run.side_effect = [subprocess.TimeoutExpired("docker", 1), subprocess.CompletedProcess([], 0)]
        with self.assertRaises(SandboxUnavailable): self.sandbox.run(["true"], name="ftrsi-test", timeout=1)
        self.assertEqual(run.call_args_list[1].args[0], ["docker", "rm", "-f", "ftrsi-test"])

    @patch("flashtrustfulrsi.sandbox.subprocess.run")
    def test_failed_workload_never_runs_on_host(self, run):
        run.return_value = subprocess.CompletedProcess([], 1, "", "unshare: operation not permitted")
        with self.assertRaises(SandboxUnavailable): self.sandbox.run(["python", "task.py"], name="ftrsi-test")
        self.assertEqual(run.call_count, 1)


if __name__ == "__main__": unittest.main()
