"""Bounded native SICA G2 development run against DeepSeek Flash."""
import argparse
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3

from .deepseek_debug import DebugBudget, DeepSeekProvider, MODEL
from .original_container import files_sha
from .provider import write_json
from .sica_evolution import checkpoint_inventory
from .sica_meta_container import run_meta
from .sica_wire import reconcile_overseer_notifications
from .transport import MODEL as NATIVE_MODEL, BudgetExceeded


DEBUG_GUIDANCE = (
    "For this bounded development smoke run, choose one small but useful coding-tool improvement, "
    "limited to one existing tool module and a focused regression check. Summarize the archive evidence "
    "concisely and use the required original review committee before implementing. Ask delegates to stop "
    "reading once they have the evidence they need, report results promptly, and avoid repeating completed "
    "checks. In the isolated image the writable candidate is /home/agent/workdir; use workdir.src imports "
    "from /home/agent. Run the tests for the actual change, using the standard library when possible. "
    "Unrelated optional benchmark dependencies, a missing git checkout, and external downloads are out of "
    "scope. Keep all seven original steps, including genuine tests, description, change log and final submission."
)


class SicaFlashAdapter:
    def __init__(self, provider, *, guided=False):
        self.provider = provider
        self.limit = provider.budget.requests
        self.guided = guided

    def invoke(self, protocol, body, request_id=None):
        if protocol != 'chat' or body.get('model') != NATIVE_MODEL:
            raise ValueError('Unexpected SICA SDK request')
        payload = dict(body)
        cap = payload.pop('max_completion_tokens', None)
        if type(cap) is not int or cap < 1 or 'max_tokens' in payload:
            raise ValueError('Expected one native completion cap')
        messages, _ = reconcile_overseer_notifications(payload['messages'])
        if self.guided:
            def main_problem(message):
                content = message.get('content')
                return (message['role']=='user' and isinstance(content, str)
                        and '<problem_statement>' in content and 'It is meta-improvement time.' in content)
            if request_id == 'debug-sica-meta-2-0001' and sum(map(main_problem, messages)) != 1:
                raise ValueError('Could not uniquely locate the SICA main problem statement for debug guidance')
            messages = [
                {**message, 'content': message['content']+'\n\n'+DEBUG_GUIDANCE}
                if main_problem(message) else message for message in messages
            ]
        payload['messages'] = messages
        payload.update(model=MODEL, max_tokens=min(cap, self.provider.budget.per_call_output),
                       thinking={'type': 'disabled'})
        return self.provider.invoke(protocol, payload, request_id=request_id)


def public_trace(provider):
    with sqlite3.connect(provider.db.resolve().as_uri()+'?mode=ro', uri=True) as db:
        rows = db.execute('SELECT call_id,request_hash,request,status,response FROM calls ORDER BY started')
        trace = []
        for call_id, digest, request, state, response in rows:
            body = json.loads(request)
            output = json.loads(response) if response else {}
            choice = (output.get('choices') or [{}])[0]
            calls = (choice.get('message') or {}).get('tool_calls') or []
            trace.append({'call_id': call_id, 'request_sha256': digest, 'state': state,
                          'response_model': output.get('model'), 'finish_reason': choice.get('finish_reason'),
                          'tool_names': [call['function']['name'] for call in calls],
                          'tool_result_messages': sum(message['role']=='tool' for message in body['messages'])})
    return trace


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--review', required=True, type=Path)
    parser.add_argument('--job-id', required=True)
    parser.add_argument('--requests', default=24, type=int, choices=(4, 12, 24, 40, 120, 200))
    parser.add_argument('--output-cap', default=4096, type=int, choices=(4096, 16384))
    parser.add_argument('--guided', action='store_true', help='Add a narrowly scoped development-only task note')
    parser.add_argument('--plan-only', action='store_true')
    args = parser.parse_args()
    if not re.fullmatch(r'dev-dsflash-sica-g2-[A-Za-z0-9-]{1,40}', args.job_id):
        parser.error('Use a separate dev-dsflash-sica-g2 job identity')
    review = json.loads(args.review.read_text())
    if review['schema'] != 'sica-committed-recovery-v1' or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', review['source_job']):
        raise ValueError('Invalid G2 source review')
    source = Path('runs')/review['source_job']/'evolution'
    if checkpoint_inventory(source) != review['inventory']:
        raise ValueError('G1 checkpoint changed')
    frozen_path = source/'meta/2/input.json'
    frozen = json.loads(frozen_path.read_text())
    digest = hashlib.sha256(json.dumps(frozen, sort_keys=True).encode()).hexdigest()
    if digest != review['next_input_sha256'] or frozen['request']['current_iter'] != 2:
        raise ValueError('Unexpected frozen G2 input')
    candidate, archive = source/'candidates/2', source/'archive'
    if files_sha(candidate) != frozen['agent_files'] or files_sha(archive) != frozen['archive_files']:
        raise ValueError('G2 candidate or archive changed')
    budget = DebugBudget(requests=args.requests, input_tokens=max(4_000_000, args.requests*200_000),
                         output_tokens=args.requests*args.output_cap, per_call_output=args.output_cap)
    manifest = {'scope': 'development_only', 'model': MODEL, 'job_id': args.job_id,
                'source_job': review['source_job'], 'g2_input_sha256': digest,
                'parent': review['next_parent'], 'actor': frozen['request']['best_iter'],
                'image': frozen['image'], 'budget': asdict(budget), 'thinking': 'disabled',
                'runs_formal_eval': False, 'promotes_candidate': False}
    if args.guided:
        manifest['debug_guidance'] = DEBUG_GUIDANCE
    if args.plan_only:
        print(json.dumps(manifest, indent=2))
        return
    if not os.environ.get('DEEPSEEK_API_KEY', '').strip():
        raise ValueError('DEEPSEEK_API_KEY is missing')
    os.umask(0o077)
    out = Path('runs')/args.job_id
    out.mkdir(mode=0o700)
    write_json(out/'manifest.json', manifest)
    provider = DeepSeekProvider(out/'provider', args.job_id, budget, timeout=180 if args.output_cap==16384 else 60)
    write_json(out/'status.json', {'status': 'running'})
    status = 'submitted_debug_candidate'
    try:
        invocation = out/'invocation'
        write_json(invocation/'input.json', frozen)
        run_meta(agent_dir=candidate, archive_dir=archive, best_iter=manifest['actor'],
                 current_iter=2, image=frozen['image'], provider=SicaFlashAdapter(provider,guided=args.guided),
                 artifact_dir=invocation, call_id='debug-sica-meta-2', timeout=frozen['request']['timeout'])
    except (RuntimeError, ValueError, OSError) as exc:
        cause = exc.__cause__
        status = 'debug_budget_reached' if isinstance(cause, BudgetExceeded) else 'failed'
        manifest['stop_reason'] = type(cause or exc).__name__ + ': ' + str(cause or exc).splitlines()[0][:200]
    report = {**manifest, 'status': status, 'ledger': provider.summary(), 'trace': public_trace(provider)}
    write_json(out/'status.json', report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if status == 'failed':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
