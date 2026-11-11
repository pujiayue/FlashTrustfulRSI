"""Exercise native SHE guard and scoring on a copied reviewed checkpoint."""
import argparse
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import re
from shutil import copytree

from .deepseek_debug import DebugBudget, DeepSeekProvider, MODEL
from .provider import write_json
from .she_evolution import MODEL as NATIVE_MODEL, file_hashes, rollout, sha


def read(path):
    return json.loads(path.read_text())


def canonical(document):
    return json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()


def review_checkpoint(root, row, plan_path):
    """Reconstruct the committed selection state without starting the formal worker."""
    previous = root/'runs'/row['worker_phase']/row['job']
    if (row['pending'] != 0 or read(previous/'status.json')['status'] != 'failed'
            or (previous/'result.json').exists() or sha(previous/'manifest.json') != row['manifest_sha256']):
        raise ValueError('SHE checkpoint no longer matches the stopped job')
    manifest = read(previous/'manifest.json')
    if sha(plan_path) != manifest['plan_sha256']:
        raise ValueError('Frozen SHE plan changed')
    plan = read(plan_path)
    job, = (item for item in plan['jobs'] if item['job_id'] == row['job'])
    if job != manifest['job'] or row['worker_phase'] != plan['phase_id']:
        raise ValueError('Frozen SHE job changed')
    rounds = [f'R{i:02d}' for i in range(row['committed_rounds'])]
    if set(row['transaction_sha256']) != {f'rounds/{name}/transaction.json' for name in rounds}:
        raise ValueError('Noncontiguous committed transactions')
    last = None
    latest_best = None
    selected_clean = None
    vetoes = []
    for name in rounds:
        path = previous/'rounds'/name
        transaction = path/'transaction.json'
        if sha(transaction) != row['transaction_sha256'][str(transaction.relative_to(previous))]:
            raise ValueError('SHE transaction changed')
        current = read(transaction)
        if last is not None and current['before'] != last:
            raise ValueError('SHE committed transaction chain changed')
        last = current['after']
        best = read(path/'best_update.json')
        if best['updated']:
            latest_best = name
            records = [json.loads(line) for line in (path/'trajectories.jsonl').read_text().splitlines()]
            clean = [item['task_completed'] for item in records if item['condition']=='clean']
            selected_clean = sum(clean)/len(clean)
        if best.get('clean_utility_veto'):
            vetoes.append({'round': name, **best})
    active = previous/'active'
    if latest_best != row['latest_best_round'] or file_hashes(active) != row['active_files_sha256'] or last != row['active_files_sha256']:
        raise ValueError('SHE selected state differs from reviewed checkpoint')
    round_name = f'R{len(rounds):02d}'
    active_rollouts = previous/'rounds'/round_name/'rollouts'
    completed = {str(path.relative_to(previous)):sha(path)
                 for path in active_rollouts.glob('*/result.json')}
    if (len(completed) != row['completed_round_06_rollouts']
            or hashlib.sha256(canonical(completed)).hexdigest() != row['completed_round_06_rollouts_sha256']):
        raise ValueError('SHE cached rollout prefix differs')
    interrupted = previous/row['invocation']
    if (interrupted.parent != active_rollouts or (interrupted/'result.json').exists()
            or sha(interrupted/'input.json') != row['input_sha256']):
        raise ValueError('SHE interrupted rollout differs')
    case = read(interrupted/'input.json')['case']
    if case not in job['train'] or not case['injection_task']:
        raise ValueError('Expected reviewed attack case')
    clean_case, = (item for item in job['train'] if item['user_task'] == case['user_task'] and item['injection_task'] is None)
    return active, (clean_case, case), {
        'source_job': row['job'], 'round': round_name, 'previous_round': rounds[-1],
        'latest_best_round': latest_best, 'selected_clean_utility': selected_clean, 'vetoes': vetoes, 'seed':job['seed'],
        'cached_rollouts': len(completed), 'interrupted_rollout': interrupted.name,
        'active_files_sha256': row['active_files_sha256'],
    }


class FlashAdapter:
    def __init__(self, provider):
        self.provider = provider

    def invoke(self, protocol, body, request_id=None):
        if protocol != 'chat' or body.get('model') != NATIVE_MODEL:
            raise ValueError('Unexpected SHE SDK payload')
        payload = {name:value for name,value in body.items() if name != 'seed'}
        messages = []
        for message in body['messages']:
            content = message.get('content')
            if isinstance(content, list):
                if any(not isinstance(part, dict) or part.get('type') != 'text'
                       or not isinstance(part.get('text'), str) for part in content):
                    raise ValueError('SHE debug only supports text message blocks')
                content = '\n'.join(part['text'] for part in content)
            role = 'system' if message['role']=='developer' else message['role']
            messages.append({**message,'role':role,'content':content})
        payload.update(model=MODEL, messages=messages, max_tokens=min(body['max_tokens'], self.provider.budget.per_call_output))
        return self.provider.invoke(protocol, payload, request_id=request_id)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inventory', required=True, type=Path)
    parser.add_argument('--plan', required=True, type=Path)
    parser.add_argument('--job-id', required=True)
    parser.add_argument('--plan-only', action='store_true')
    args = parser.parse_args()
    if not re.fullmatch(r'dev-dsflash-she-[A-Za-z0-9-]{1,40}', args.job_id):
        parser.error('Use a dedicated SHE development identity')
    root = Path.cwd()
    row, = (item for item in read(args.inventory)['jobs'] if item['action']=='checkpoint_round_06')
    active, pair, checkpoint = review_checkpoint(root, row, args.plan)
    budget = DebugBudget(requests=12, input_tokens=500_000, output_tokens=32_768, per_call_output=4096)
    report = {'scope':'development_only', 'model': MODEL, 'job_id':args.job_id,
              'checkpoint':checkpoint, 'cases':[case['case_id'] for case in pair],
              'budget_per_arm':asdict(budget), 'seed_forwarded':False, 'runs_formal_eval':False,
              'chat_translation':'text blocks joined; developer role mapped to system'}
    if args.plan_only:
        print(json.dumps(report, indent=2))
        return
    if not os.environ.get('DEEPSEEK_API_KEY', '').strip():
        raise ValueError('DEEPSEEK_API_KEY is missing')
    os.umask(0o077)
    out = root/'runs'/args.job_id
    out.mkdir()
    write_json(out/'manifest.json', report)
    assets = out/'active'
    copytree(active, assets)
    if file_hashes(assets) != checkpoint['active_files_sha256']:
        raise ValueError('Copied active artifacts changed')
    report.update(status='running', arms=[])
    write_json(out/'status.json', report)
    for name, case in zip(('clean', 'injected'), pair):
        provider = DeepSeekProvider(out/name/'provider', f'{args.job_id}-{name}', budget)
        arm = {'arm': name, 'case':case['case_id']}
        try:
            data = rollout(FlashAdapter(provider), case, assets, checkpoint['seed'], f'debug-{name}-r0', out/name/'rollout')
            arm.update(status='observed' if not data['failed'] else 'unobserved', utility=data['utility'],
                       attack_success=data['attack_success'], error=data['error'],
                       tool_events=[{'name':tool['name'], 'blocked':tool['blocked']} for tool in data['tool_calls']])
        except (RuntimeError, ValueError, OSError) as exc:
            arm.update(status='unobserved', error=type(exc).__name__+': '+str(exc).splitlines()[0][:160])
        arm['ledger']=provider.summary()
        report['arms'].append(arm)
        write_json(out/'status.json', report)
    if file_hashes(assets) != checkpoint['active_files_sha256']:
        raise ValueError('SHE runtime modified the frozen debug artifacts')
    report['status'] = 'debug_complete' if all(arm['status']=='observed' for arm in report['arms']) else 'debug_incomplete'
    write_json(out/'status.json', report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report['status']!='debug_complete':
        raise SystemExit(1)


if __name__=='__main__':
    main()
