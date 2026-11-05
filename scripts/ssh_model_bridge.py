"""Relay one YAML twins job between two SSH hosts without moving API credentials.

Uses configured SSH aliases/agents (BatchMode). No keys or passwords are accepted.
Run model_rpc.py only on the original model credential host.
"""
import argparse, fcntl, json, shlex, subprocess, time
from pathlib import Path


def remote(alias, root, python, script, args, body=None, control=None):
    ssh = ['ssh', '-o', 'ClearAllForwardings=yes', '-o', 'BatchMode=yes']
    if alias.startswith('-') or not alias:
        raise ValueError('Expected SSH alias, not an option')
    if control:
        ssh += ['-S', control]
    command = 'cd ' + shlex.quote(root) + ' && ' + shlex.join(
        ['env', 'PYTHONPATH=src', python, 'scripts/' + script, *args])
    result = subprocess.run([*ssh, alias, command],
        input=json.dumps(body, ensure_ascii=False) if body is not None else None,
        capture_output=True, text=True, timeout=240)
    if not result.stdout:
        raise RuntimeError('SSH returned no structured response; reconcile before restarting')
    document = json.loads(result.stdout)
    if result.returncode and document.get('ok') is not False:
        raise RuntimeError('SSH command failed; no automatic retry')
    return document


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('job-id', 'worker-host', 'worker-root', 'model-host', 'model-root', 'lock'):
        p.add_argument('--' + name, required=True)
    p.add_argument('--worker-python', default='python3')
    p.add_argument('--model-python', default='.venv/bin/python')
    p.add_argument('--worker-control-path')
    p.add_argument('--deadline-seconds', type=int, default=3600)
    a = p.parse_args()
    if not 1 <= a.deadline_seconds <= 86400:
        raise ValueError('Invalid bridge deadline')
    with Path(a.lock).open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        deadline = time.monotonic() + a.deadline_seconds
        while time.monotonic() < deadline:
            item = remote(a.worker_host, a.worker_root, a.worker_python, 'spool_io.py',
                ['next', '--job-id', a.job_id], control=a.worker_control_path)
            request = item['request']
            if request is None:
                if item['status']['status'] in ('completed', 'failed'):
                    print(json.dumps(item['status']), flush=True)
                    return
                time.sleep(2)
                continue
            if request['job_id'] != a.job_id:
                raise ValueError('Unexpected job identity')
            response = remote(a.model_host, a.model_root, a.model_python, 'model_rpc.py',
                ['--root', a.model_root, '--job-id', a.job_id], request)
            stored = remote(a.worker_host, a.worker_root, a.worker_python, 'spool_io.py',
                ['put', '--job-id', a.job_id], {'request': request, 'response': response},
                control=a.worker_control_path)
            print(json.dumps(stored), flush=True)
            if not response['ok']:
                raise RuntimeError('Recorded provider stopped; inspect original ledger')
        raise TimeoutError('Bridge deadline reached; pending requests preserved')


if __name__ == '__main__':
    main()
