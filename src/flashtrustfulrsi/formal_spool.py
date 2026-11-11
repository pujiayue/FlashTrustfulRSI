"""Execute one untouched frozen formal job through the credential-free spool."""
import argparse
import hashlib
import importlib
import json
from pathlib import Path
import re
import time

from .native_spool import NativeSpoolProvider
from .provider import write_json
from .transport import MODEL


MODULES = {'evopath': ('evopath_formal', 'ReviewedProvider'),
           'dojo': ('dojo_formal', 'RecordedNativeProvider')}


class FormalProvider:
    def __init__(self, root, job_id, limits, phase):
        self.spool = NativeSpoolProvider(root / 'runs/model-spool', job_id, timeout=600)
        self.sequence = 0
        self.limits = limits
        self.phase = phase
        self.states = {}
        self.input_tokens = self.output_tokens = self.uncertain = 0

    def invoke(self, protocol, body, request_id=None):
        self.sequence += 1
        if self.sequence > self.limits['requests']:
            raise RuntimeError('Frozen formal request limit reached before spooling')
        identity = request_id or f'{self.sequence:08d}'
        try:
            response = self.spool.invoke(protocol, body, request_id=identity)
        except Exception:
            self.states['failed'] = self.states.get('failed', 0) + 1
            self.uncertain += 1
            raise
        self.states['success'] = self.states.get('success', 0) + 1
        usage = response.get('usage') or {}
        prompt, completion = usage.get('prompt_tokens'), usage.get('completion_tokens')
        if all(type(n) is int and n >= 0 for n in (prompt, completion)):
            self.input_tokens += prompt
            self.output_tokens += completion
        else:
            self.uncertain += 1
        return response

    def summary(self):
        return {'model': MODEL, 'states': dict(self.states), 'logical_states': dict(self.states),
                'input_tokens': self.input_tokens, 'output_tokens': self.output_tokens,
                'requests': self.sequence, 'uncertain_requests': self.uncertain,
                'authoritative_accounting': self.phase, 'usage_source': 'successful provider-reported usage only'}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', default='.')
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--module', choices=MODULES, required=True)
    parser.add_argument('--job-id', required=True)
    parser.add_argument('--model-phase', required=True)
    parser.add_argument('--plan-only', action='store_true')
    args = parser.parse_args()
    root = Path(args.root).resolve()
    plan = args.plan.resolve()
    doc = json.loads(plan.read_text())
    if any(not re.fullmatch('[A-Za-z0-9_-]{1,80}', name)
           for name in (args.job_id, args.model_phase, doc['phase_id'])):
        raise ValueError('Invalid frozen job or phase identity')
    job, = (j for j in doc['jobs'] if j['job_id'] == args.job_id)
    if (doc.get('recovery_review') or {}).get('job_id') == args.job_id:
        raise ValueError('This entry requires an untouched job without recovery ancestry')
    module_name, provider_attr = MODULES[args.module]
    native = importlib.import_module('.'+module_name, __package__)
    if sha(Path(native.__file__)) != doc['adapter_sha256']:
        raise ValueError('Frozen adapter hash does not match 147 checkout')
    dest = root / 'runs' / doc['phase_id'] / args.job_id
    limits = job['limits'] if 'limits' in job else doc['limits_per_job']
    provenance = {'phase_id': args.model_phase, 'model': MODEL, 'authorized_jobs': [args.job_id],
                  'protocols': ['chat'], 'limits': limits, 'plan_sha256': sha(plan),
                  'spool_adapter_sha256': sha(Path(__file__))}
    if args.plan_only:
        print(json.dumps(provenance, indent=2))
        return
    config = json.loads((root / 'configs/provider-v02.json').read_text())
    if config != {'model': MODEL, 'base_url': 'https://configured-gateway.example/v1'}:
        raise ValueError('Expected a credential-free project configuration')
    if (root / 'runs/model-spool' / args.job_id).exists():
        raise ValueError('Existing spool requires explicit reconciliation')
    dest.mkdir(parents=True)
    write_json(dest / 'worker-transport.json', provenance)

    def provider(config, directory, job_id, limits, review=None, project_root=None):
        if review or job_id != args.job_id:
            raise ValueError('Unexpected job/recovery identity')
        return FormalProvider(root, job_id, limits, args.model_phase)

    setattr(native, provider_attr, provider)
    try:
        native.run_job(root, plan, args.job_id)
    except Exception as exc:
        if not (dest / 'status.json').exists():
            write_json(dest / 'status.json', {'status': 'failed', 'error_type': type(exc).__name__,
                       'error': str(exc), 'finished': time.time()})
        raise


if __name__ == '__main__':
    main()
