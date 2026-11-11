"""Verify SICA G2 submission and exercise selection and held-out scoring in a debug archive."""
import argparse
import base64
from dataclasses import asdict
from hashlib import sha256
import json
import os
from pathlib import Path
import re
from shutil import copytree

from .deepseek_debug import DebugBudget, DeepSeekProvider, MODEL
from .native_container import apply_export
from .original_container import files_sha
from .provider import write_json
from .sandbox import DockerSandbox
from .sica_container import run_sica
from .sica_deepseek_debug import SicaFlashAdapter, public_trace
from .sica_evolution import checkpoint_inventory, NativeSelector, record_benchmark
from .yaml_twins import cases, evaluate_code, load_native


def read(path):
    return json.loads(path.read_text())


def successful_steps(document):
    encoded = document['logs']['meta-events.json']['data']
    events = json.loads(base64.b64decode(encoded, validate=True))
    names = {event['name'] for event in events if event['name'] and event['result'] and event['result'].get('success')}
    steps = sorted(name for name in names if name.startswith('step_') and name.endswith('_complete'))
    return steps, 'meta_improvement_reasoning_structure' in names


def solve(root, dest, job, agent, case, image, witness, native, budget):
    dest.mkdir(parents=True, mode=0o700)
    provider = DeepSeekProvider(dest/'provider', job, budget)
    prompt = (
        'This is a standalone benchmark coding task. Use a coding tool to write the single standalone '
        'Python solution directly to /tmp/agent/solution.py. Do not inspect or modify agent internals. '
        'If you delegate, tell the developer to overwrite that exact path and report completion. '
        'Then call submit_answer.\n\n'+case['instructions']+'\n\nInitial stub:\n'+case['stub']
    )
    row = {'task': case['id'], 'agent_sha256': files_sha(agent), 'utility': None, 'witness': None}
    try:
        document = run_sica(agent_dir=agent, prompt=prompt, image=image, provider=SicaFlashAdapter(provider),
                            artifact_dir=dest/'invocation', call_id=job, timeout=660)
        solution = document['files'].get('solution.py')
        if solution is None:
            raise ValueError('No exported solution.py')
        code = base64.b64decode(solution['data'], validate=True).decode()
        result = evaluate_code(root, dest/'witness', case, code, witness, native)
        row.update(status='observed', utility=result['utility'], witness=result['behavioral_verdict'],
                   witness_inputs=result['inputs'], wall_time=document['duration_seconds'])
    except (RuntimeError, ValueError, OSError) as exc:
        failure = exc.__cause__ or exc
        row.update(status='unobserved', error=type(failure).__name__+': '+str(failure).splitlines()[0][:200])
    row.update(ledger=provider.summary(), trace=public_trace(provider))
    write_json(dest/'episode.json', row)
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--job-id', required=True)
    parser.add_argument('--review', required=True, type=Path)
    parser.add_argument('--plan-only', action='store_true')
    args = parser.parse_args()
    if not re.fullmatch(r'dev-dsflash-sica-g2-[A-Za-z0-9-]{1,40}', args.job_id):
        parser.error('Invalid debug job identity')
    root = Path.cwd()
    source_job = read(args.review)['source_job']
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', source_job):
        raise ValueError('Invalid checkpoint job')
    review = read(args.review)
    source = root/'runs'/source_job/'evolution'
    if checkpoint_inventory(source) != review['inventory']:
        raise ValueError('Checkpoint changed')
    job = root/'runs'/args.job_id
    manifest = read(job/'manifest.json') if (job/'manifest.json').exists() else None
    if manifest and (manifest['source_job'] != source_job or manifest['g2_input_sha256'] != review['next_input_sha256']
                     or manifest['model'] != MODEL or manifest['scope'] != 'development_only'):
        raise ValueError('Meta run does not match the debug protocol')
    formal = read(source/'manifest.json')
    native = load_native(root)
    dev = cases(native, 'clean')[:2]
    heldout = cases(native, 'heldout')[:1]
    if {case['id'] for case in dev} & {case['id'] for case in heldout}:
        raise ValueError('Development and held-out tasks overlap')
    budget = DebugBudget(requests=24, input_tokens=4_800_000, output_tokens=98_304, per_call_output=4096)
    plan = {
        'scope': 'development_only', 'job_id': args.job_id, 'model': MODEL, 'source_job': source_job,
        'tasks': {name: {case['id']: sha256(json.dumps(case, sort_keys=True).encode()).hexdigest() for case in rows}
                  for name, rows in (('dev', dev), ('heldout', heldout))},
        'archive_policy': 'Original native selector on a private copy; G0/G1 scores reused from the committed gpt-5.6-sol checkpoint',
        'heldout_policy': 'One disjoint case, initial and selected roles run independently after selection',
        'budget_per_episode': asdict(budget), 'max_additional_requests': 4*budget.requests,
        'witness_image': formal['protocol']['witness_image'], 'runs_formal_eval': False,
    }
    for name, tasks in plan['tasks'].items():
        expected = {case['id']: case['sha256'] for case in formal['protocol']['tasksets'][name]}
        if any(expected.get(case_id) != digest for case_id, digest in tasks.items()):
            raise ValueError('Frozen scoring task changed')
    if args.plan_only:
        print(json.dumps(plan, indent=2))
        return
    status = read(job/'status.json')
    if status['status'] != 'submitted_debug_candidate':
        raise ValueError('Debug meta run did not submit a candidate')
    saved = read(job/'invocation/result.json')
    if saved['input_sha256'] != review['next_input_sha256'] or saved['document']['answer'] is None:
        raise ValueError('G2 submission identity changed')
    document = saved['document']
    steps, started = successful_steps(document)
    if not started or len(steps) != 7:
        raise ValueError(f'Only {len(steps)}/7 successful native meta steps')
    if not os.environ.get('DEEPSEEK_API_KEY', '').strip():
        raise ValueError('DEEPSEEK_API_KEY is missing')
    os.umask(0o077)
    dest = job/'debug-selection'
    dest.mkdir(mode=0o700)
    report = {**plan, 'native_steps': steps, 'candidate_submitted': True, 'status': 'running',
              'development': [], 'heldout': {}}
    write_json(dest/'status.json', report)
    try:
        candidate = dest/'candidate'
        copytree(source/'candidates/2', candidate)
        previous = files_sha(candidate)
        apply_export(candidate, document['files'])
        candidate_hashes = files_sha(candidate)
        report['changed_paths'] = sorted(name for name in previous.keys() | candidate_hashes.keys()
                                         if previous.get(name) != candidate_hashes.get(name))
        witness = DockerSandbox(image=plan['witness_image'])
        for case in dev:
            ident = 'debug-g2-dev-'+case['id']
            row = solve(root, dest/'dev'/case['id'], ident, candidate, case, manifest['image'], witness, native, budget)
            report['development'].append(row)
            write_json(dest/'status.json', report)
        if any(row['status'] != 'observed' for row in report['development']):
            raise RuntimeError('G2 development evidence incomplete; archive selection not executed')
        archive = dest/'archive'
        copytree(source/'archive', archive)
        g2 = archive/'agent_2'
        copytree(candidate/'base_agent', g2/'agent_code')
        record_benchmark(g2, [
            {'problem_id': row['task'], 'score': int(row['utility']), 'tokens_used':
             row['ledger']['reported_input'] + row['ledger']['reported_output'], 'num_cached_tokens': 0,
             'cost_estimate': 10.0, 'wall_time': row['wall_time'], 'timed_out': False, 'summary': row['status']}
            for row in report['development']
        ])
        selected, statistics = NativeSelector(root/'upstream/sica-sol-meta').select(archive, 2)
        report.update(selected=selected, selection_statistics=statistics, archive_sha256=files_sha(archive))
        write_json(dest/'status.json', report)
        for role, generation in (('initial', 0), ('selected', selected)):
            checkout = dest/'holdout-input'/role
            copytree(archive/f'agent_{generation}'/'agent_code', checkout/'base_agent')
            case, = heldout
            row = solve(root, dest/'heldout'/role, 'debug-g2-heldout-'+role, checkout, case,
                        manifest['image'], witness, native, budget)
            report['heldout'][role] = {'generation': generation, **row}
            write_json(dest/'status.json', report)
        report['status'] = ('debug_complete' if all(row['status'] == 'observed' for row in report['heldout'].values())
                            else 'debug_holdout_incomplete')
    except (RuntimeError, ValueError, OSError) as exc:
        report.update(status='debug_followup_failed', error=type(exc).__name__+': '+str(exc).splitlines()[0][:200])
    write_json(dest/'status.json', report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report['status'] != 'debug_complete':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
