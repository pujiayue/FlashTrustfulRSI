"""Bridge untouched guarded SHE and native CIM label jobs from 147."""
import argparse
import json
from pathlib import Path
import re

from .formal_spool import FormalProvider, sha
from .provider import write_json
from .transport import MODEL


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--module', required=True, choices=('cim-labels', 'she'))
    parser.add_argument('--plan', required=True, type=Path)
    parser.add_argument('--job-id', required=True)
    parser.add_argument('--model-phase', required=True)
    parser.add_argument('--plan-only', action='store_true')
    args = parser.parse_args()
    root = Path.cwd()
    plan = args.plan.resolve()
    doc = json.loads(plan.read_text())
    if any(not re.fullmatch('[A-Za-z0-9_-]{1,80}', name)
           for name in (args.job_id, args.model_phase, doc['phase_id'])):
        raise ValueError('Invalid job/phase identity')
    if sum(item['job_id'] == args.job_id for item in doc['jobs']) != 1:
        raise ValueError('Unknown job in frozen plan')

    def provider(config, directory, job_id, limits):
        if job_id != args.job_id or config != {'model': MODEL, 'base_url': 'https://configured-gateway.example/v1'}:
            raise ValueError('Unexpected worker job/configuration')
        return FormalProvider(root, job_id, limits, args.model_phase)

    if args.module == 'cim-labels':
        from . import cim_native_labels as native
        from . import durable_transport as shim
        expected = doc['native_label_wrapper_sha256']
        if args.job_id in doc.get('reuse', {}):
            raise ValueError('Only untouched label jobs may use this entry')
    else:
        from . import she_guarded_evolution as native
        shim = native
        expected = doc['adapter_files']['she_guarded_evolution.py']
        if args.job_id in doc['reused_seed_observations']:
            raise ValueError('Existing SHE ancestry requires explicit reconciliation')
    if sha(Path(native.__file__)) != expected:
        raise ValueError('Frozen wrapper mismatch')
    policy = {'phase_id': args.model_phase, 'model': MODEL, 'authorized_jobs': [args.job_id],
              'protocols': ['chat'], 'limits': doc['limits_per_job'], 'plan_sha256': sha(plan),
              'spool_adapter_sha256': sha(Path(__file__))}
    if args.plan_only:
        print(json.dumps(policy, indent=2))
        return
    spool = root / 'runs/model-spool' / args.job_id
    if spool.is_dir() and any(spool.iterdir()):
        raise ValueError('Existing spool requires explicit reconciliation')
    dest = root / 'runs' / doc['phase_id'] / args.job_id
    dest.mkdir(parents=True)
    write_json(dest/'worker-transport.json', policy)
    shim.DurableProvider = provider
    try:
        native.run_job(root, plan, args.job_id)
    except Exception as exc:
        status = dest/'status.json'
        if not status.exists() or json.loads(status.read_text())['status'] != 'failed':
            write_json(status, {'status':'failed','error_type':type(exc).__name__,'error':str(exc)})
        raise


if __name__ == '__main__':
    main()
