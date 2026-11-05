"""Docker execution contract. Never falls back to executing a workload on the host."""
import argparse
import json
import os
import re
import selectors
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

from .provider import write_json


class SandboxUnavailable(RuntimeError):
    pass


class DockerSandbox:
    def __init__(self, executable="docker", host=None, image=None):
        self.executable = executable
        self.host = host
        self.image = image

    def prefix(self):
        return [self.executable] + (["--host", self.host] if self.host else [])

    def command(self, command, *, name, workdir=None, token=None, cidfile=None):
        if not self.image or not re.search(r"(?:^sha256:|@sha256:)[0-9a-f]{64}$", self.image):
            raise ValueError("Use an immutable local image ID or repository@sha256 digest")
        if not re.fullmatch(r"ftrsi-[a-z0-9-]{1,50}", name):
            raise ValueError("Project-scoped container name required")
        if not command or any(not isinstance(x, str) for x in command):
            raise ValueError("Explicit argv required")
        argv = self.prefix() + [
            "run", "--pull=never", "--rm", "--name", name,
            "--label=org.flashtrustfulrsi.owner=FlashTrustfulRSI", "--log-driver=none",
            "--network=none", "--read-only", "--cpus=1", "--memory=512m",
            "--memory-swap=512m", "--pids-limit=64", "--cap-drop=ALL",
            "--security-opt=no-new-privileges:true", "--user=65534:65534",
            "--tmpfs=/tmp:rw,noexec,nosuid,size=64m", "--init",
        ]
        if token:
            argv += ["--label=org.flashtrustfulrsi.invocation=" + token]
        if cidfile:
            argv += ["--cidfile", str(cidfile)]
        if workdir:
            path = Path(workdir).resolve(strict=True)
            if not path.is_dir() or any(c in str(path) for c in (",", "\n")):
                raise ValueError("Invalid isolated workspace")
            argv += ["--mount", f"type=bind,source={path},target=/workspace,readonly", "--workdir=/workspace"]
        return argv + [self.image, *command]

    def _execute(self, argv, timeout, output_limit=4*1024*1024):
        """Bound host-side output as well as container runtime; no shell wrapper."""
        process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        buffers = {"stdout": bytearray(), "stderr": bytearray()}
        deadline = time.monotonic() + timeout
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ, "stdout")
            selector.register(process.stderr, selectors.EVENT_READ, "stderr")
            try:
                while selector.get_map():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise SandboxUnavailable("Container runtime deadline exceeded")
                    for key, _ in selector.select(min(remaining, .2)):
                        chunk = os.read(key.fileobj.fileno(), 65536)
                        if not chunk:
                            selector.unregister(key.fileobj); continue
                        if sum(map(len, buffers.values())) + len(chunk) > output_limit:
                            raise SandboxUnavailable("Container output limit exceeded")
                        buffers[key.data].extend(chunk)
                process.wait(timeout=max(.01, deadline-time.monotonic()))
                return subprocess.CompletedProcess(argv, process.returncode,
                    buffers["stdout"].decode(errors="replace"), buffers["stderr"].decode(errors="replace"))
            finally:
                if process.poll() is None:
                    process.kill(); process.wait(timeout=5)
                process.stdout.close(); process.stderr.close()

    def _cleanup(self, cidfile, token):
        # Never remove a container by a caller-chosen name. The ID must be
        # created by this invocation AND carry the matching ownership labels.
        if not cidfile.exists(): return
        container_id = cidfile.read_text().strip()
        if not re.fullmatch("[0-9a-f]{64}", container_id): return
        inspected = subprocess.run(self.prefix()+["inspect", "--format", "{{json .Config.Labels}}", container_id],
                                   capture_output=True, text=True, timeout=15)
        if inspected.returncode: return  # --rm may already have removed it.
        labels = json.loads(inspected.stdout) or {}
        if labels.get("org.flashtrustfulrsi.owner") != "FlashTrustfulRSI" or labels.get("org.flashtrustfulrsi.invocation") != token:
            raise SandboxUnavailable("Ownership mismatch; container left untouched")
        removed = subprocess.run(self.prefix()+["rm", "-f", container_id], capture_output=True, text=True, timeout=15)
        if removed.returncode:
            raise SandboxUnavailable("Owned container cleanup failed: "+removed.stderr[-500:])

    def run_process(self, command, *, name, workdir=None, timeout=60):
        self.command(command, name=name, workdir=workdir)  # Validate before deriving unique name.
        token = uuid.uuid4().hex
        with tempfile.TemporaryDirectory(prefix="ftrsi-container-") as temp:
            cidfile = Path(temp)/"cid"
            argv = self.command(command, name=name[:35]+"-"+token[:12], workdir=workdir, token=token, cidfile=cidfile)
            try:
                completed = self._execute(argv, timeout)
            except (OSError, subprocess.SubprocessError) as exc:
                raise SandboxUnavailable(type(exc).__name__) from None
            finally:
                self._cleanup(cidfile, token)
        return completed

    def run(self, command, *, name, workdir=None, timeout=60):
        completed = self.run_process(command, name=name, workdir=workdir, timeout=timeout)
        if completed.returncode:
            raise SandboxUnavailable(completed.stderr[-3000:])
        return completed.stdout

    def probe(self):
        report = {"schema": "flashtrustfulrsi.sandbox-probe.v1", "timestamp": time.time(),
                  "daemon_available": False, "execution_verified": False, "limits_verified": False,
                  "host": self.host or "default", "image": self.image,
                  "status": "blocked", "host_fallback": False}
        try:
            result = subprocess.run(self.prefix() + ["info", "--format", "{{json .}}"],
                                    capture_output=True, text=True, timeout=15, check=True)
            info = json.loads(result.stdout)
            report.update(daemon_available=True, server_version=info.get("ServerVersion"), driver=info.get("Driver"))
            if not self.image:
                report["reason"] = "No immutable probe image; daemon connectivity is not execution verification"
                return report
            script = ("set -eu; test ! -w /; "
                      "printf 'memory='; cat /sys/fs/cgroup/memory.max; "
                      "printf 'pids='; cat /sys/fs/cgroup/pids.max; "
                      "printf 'cpu='; cat /sys/fs/cgroup/cpu.max; "
                      "printf 'uid='; id -u; printf 'cap='; sed -n 's/^CapEff:[[:space:]]*//p' /proc/self/status; "
                      "printf 'nnp='; sed -n 's/^NoNewPrivs:[[:space:]]*//p' /proc/self/status; "
                      "printf 'seccomp='; sed -n 's/^Seccomp:[[:space:]]*//p' /proc/self/status; "
                      "printf 'rootmount='; awk '$5==\"/\" {print $6}' /proc/self/mountinfo; "
                      "printf 'interfaces='; ls /sys/class/net")
            output = self.run(["sh", "-c", script], name="ftrsi-capability-probe", timeout=30)
            report.update(execution_verified=True, probe_stdout=output)
            values = dict(line.split("=", 1) for line in output.splitlines() if "=" in line)
            cpu = values.get("cpu", "").split()
            report["limits_verified"] = (
                values.get("memory") == str(512 * 1024 * 1024) and values.get("pids") == "64"
                and len(cpu) == 2 and cpu[0] != "max" and int(cpu[0]) <= int(cpu[1])
                and values.get("uid") == "65534" and int(values.get("cap", "1"), 16) == 0
                and values.get("nnp") == "1" and values.get("seccomp") == "2"
                and "ro" in values.get("rootmount", "").split(",") and values.get("interfaces") == "lo")
            report["status"] = "ready" if report["limits_verified"] else "blocked"
            if not report["limits_verified"]: report["reason"] = "Resource/isolation assertions failed"
        except (OSError, ValueError, subprocess.SubprocessError, SandboxUnavailable) as exc:
            report["reason"] = str(exc)[-3000:]
        return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docker", default="docker")
    parser.add_argument("--docker-host")
    parser.add_argument("--image")
    parser.add_argument("--output")
    args = parser.parse_args()
    report = DockerSandbox(args.docker, args.docker_host, args.image).probe()
    if args.output: write_json(Path(args.output), report)
    print(json.dumps(report, indent=2))


if __name__ == "__main__": main()
