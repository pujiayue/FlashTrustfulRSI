"""Relay one YAML twins job between two SSH hosts without moving API credentials.

Uses configured SSH aliases/agents (BatchMode). No keys or passwords are accepted.
Run model_rpc.py only on the original model credential host.
"""
import argparse, fcntl, json, shlex, subprocess, time
from pathlib import Path


class SSHUnavailable(RuntimeError):
    pass


def remote(alias, root, python, script, args, body=None, control=None, timeout=900):
    ssh = ['ssh', '-o', 'ClearAllForwardings=yes', '-o', 'BatchMode=yes']
    if alias.startswith('-') or not alias:
        raise ValueError('Expected SSH alias, not an option')
    if control:
        ssh += ['-S', control]
    command = 'cd ' + shlex.quote(root) + ' && ' + shlex.join(
        ['env', 'PYTHONPATH=src', python, 'scripts/' + script, *args])
    try:
        result = subprocess.run([*ssh, alias, command],
            input=json.dumps(body, ensure_ascii=False) if body is not None else None,
            capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise SSHUnavailable('SSH command timed out') from exc
    if not result.stdout:
        raise SSHUnavailable('SSH returned no structured response; reconcile before restarting')
    document = json.loads(result.stdout)
    if result.returncode and document.get('ok') is not False:
        raise RuntimeError('SSH command failed; no automatic retry')
    return document


def worker_rpc(alias, root, python, action, args, *, body=None, control=None):
    # Polling is read-only apart from creating an empty spool. Delivery reuses the
    # exact retained response; spool_io verifies hashes and rejects changes.
    for attempt in range(3):
        try:
            return remote(alias, root, python, 'spool_io.py', [action, *args],
                          body=body, control=control, timeout=25)
        except SSHUnavailable:
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('job-id', 'worker-host', 'worker-root', 'model-host', 'model-root', 'lock'):
        p.add_argument('--' + name, required=True)
    p.add_argument('--worker-python', default='python3')
    p.add_argument('--model-python', default='.venv/bin/python')
    p.add_argument('--worker-control-path')
    p.add_argument('--model-control-path')
    p.add_argument('--native-phase-id', help='Use the separately frozen native SDK policy on the model host')
    p.add_argument('--worker-phase-id', help='Status directory for a nested formal campaign')
    p.add_argument('--recovery-review', help='Frozen exact-call recovery review on the original model host')
    p.add_argument('--storage-recovery-review', help='Explicit independent attempt review for unrecoverable storage failures')
    p.add_argument('--allow-sica-empty-retry', action='store_true')
    p.add_argument('--deadline-seconds', type=int, default=3600)
    a = p.parse_args()
    if not 1 <= a.deadline_seconds <= 86400:
        raise ValueError('Invalid bridge deadline')
    if a.allow_sica_empty_retry and (not a.job_id.startswith('owner-live-sica-meta-') or a.native_phase_id != a.job_id):
        raise ValueError('Empty-response recovery requires its own SICA phase')
    with Path(a.lock).open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        deadline = time.monotonic() + a.deadline_seconds
        phase_args=['--phase-id',a.worker_phase_id] if a.worker_phase_id else []
        while time.monotonic() < deadline:
            item = worker_rpc(a.worker_host, a.worker_root, a.worker_python, 'next',
                ['--job-id', a.job_id,*phase_args], control=a.worker_control_path)
            request = item['request']
            if request is None:
                if item['status']['status'] in ('completed', 'failed'):
                    print(json.dumps(item['status']), flush=True)
                    return
                time.sleep(2)
                continue
            if request['job_id'] != a.job_id:
                raise ValueError('Unexpected job identity')
            rpc_args=['--root',a.model_root,'--job-id',a.job_id]
            if a.native_phase_id:rpc_args+=['--phase-id',a.native_phase_id]
            if a.recovery_review:rpc_args+=['--recovery-review',a.recovery_review]
            if a.storage_recovery_review:rpc_args+=['--storage-recovery-review',a.storage_recovery_review]
            response = remote(a.model_host, a.model_root, a.model_python,
                'native_model_rpc.py' if a.native_phase_id else 'model_rpc.py',rpc_args,request,control=a.model_control_path)
            stored = worker_rpc(a.worker_host, a.worker_root, a.worker_python, 'put',
                ['--job-id', a.job_id,*phase_args], body={'request': request, 'response': response},
                control=a.worker_control_path)
            print(json.dumps(stored), flush=True)
            if not response['ok']:
                if (a.allow_sica_empty_retry and response['error']=='TransportError: Empty generation'
                        and request['call_id'].startswith('sica-meta-') and '-retry-empty-' not in request['call_id']):
                    continue
                raise RuntimeError('Recorded provider stopped; inspect original ledger')
        raise TimeoutError('Bridge deadline reached; pending requests preserved')


if __name__ == '__main__':
    main()
