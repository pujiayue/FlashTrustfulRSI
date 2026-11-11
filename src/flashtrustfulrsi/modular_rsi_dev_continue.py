"""Resume a reviewed ModularRSI development cutoff at its native integration proposal."""

from collections import Counter
import json
import random
import shutil

from .deepseek_debug import CHAT_URL, MODEL, ledger_status
from .searchqa_formal import sha


def read(path):
    return json.loads(path.read_text())


class IntegrationCutoff:
    """Only accept the no-admission, successful merge gate boundary."""

    def __init__(self, source, spec):
        self.source = source
        manifest = read(source/'manifest.json')
        fields = ('scope', 'method', 'model', 'native_commit', 'seed', 'image',
                  'data_sha256', 'splits', 'native_config', 'worker', 'selection', 'transport')
        status = read(source/'status.json')
        if (manifest['job_id'] != source.name or manifest['runs_formal_eval'] is not False
                or any(manifest[key] != spec[key] for key in fields)
                or status != {'status':'running', 'phase':'native_evolution'}
                or (source/'result.json').exists()):
            raise ValueError('Source is not the reviewed ModularRSI integration cutoff')

        ledger = ledger_status(source/'provider')
        calls = ledger['calls']
        worker = source/'invocations/worker-000009'
        if (ledger['job_id'] != source.name or ledger['model'] != MODEL or ledger['endpoint'] != CHAT_URL
                or ledger['usage_unknown_calls'] or Counter(call['state'] for call in calls) != {'success':82}
                or calls[-1]['call_id'] != 'worker-000009-0025'
                or (worker/'result.json').exists() or (worker/'process.json').exists()):
            raise ValueError('Source receipts or incomplete invocation differ from the reviewed cutoff')

        self.records = sorted((source/'native/trajectories').glob('*.json'))
        if len(self.records) != 30 or [int(path.name[:5]) for path in self.records] != list(range(1,31)):
            raise ValueError('Source has an unexpected native record sequence')
        groups = [read(path) for path in self.records if path.name.endswith('-contrast_groups.json')]
        decisions = [read(path) for path in self.records if path.name.endswith('-decision.json')]
        gate = read(source/'native/trajectories/00027-gate.json')
        self.snapshot = gate['snapshot_id']
        self.modules = list(spec['native_config']['modules'])
        if ([row['module'] for row in groups] != [*self.modules,'integration']
                or any(row['snapshot_id'] != self.snapshot for row in groups)
                or decisions != [{'kind':'decline','module':'integration','parent_id':self.snapshot,
                                  'problem':'harness unchanged'}]
                or gate != {'module':'integration','snapshot_id':self.snapshot,'passed':True}):
            raise ValueError('Native branch admissions, merge, or integration gate changed')
        draft = read(worker/'input.json')
        self.prompt, separator, refs = draft['prompt'].rpartition('\nModule reference directories (do not edit): ')
        self.worker_snapshot = read(source/'invocations/worker-000008/input.json')['snapshot_id']
        if (not separator or list(json.loads(refs)) != self.modules or draft['snapshot_id'] != self.worker_snapshot
                or not draft['editing'] or not self.prompt.startswith('[ModularRSI: integrate]')):
            raise ValueError('Missing exact native integration proposal prompt')
        self.review = {'source_job':source.name,'source_manifest_sha256':sha(source/'manifest.json'),
                       'source_snapshot':self.snapshot,'source_native_records':{p.name:sha(p) for p in self.records},
                       'interrupted_input_sha256':sha(worker/'input.json'),'prior_successful_requests':82,
                       'interrupted_worker_successful_requests':25,
                       'boundary':'reuse the completed merge gate and integration contrast; restart only the incomplete proposal invocation'}

    async def finish(self, algorithm, initial):
        from flashrsi.core.snapshot import Snapshot

        source_snapshot = Snapshot.load(self.source/'initial.json')
        if (initial.id != self.snapshot or source_snapshot.id != initial.id
                or algorithm.worker.snapshot.id != self.worker_snapshot):
            raise ValueError('Continuation initial snapshot differs from the verified merge gate')
        for path in self.records:
            shutil.copyfile(path,algorithm._run_dir/'trajectories'/path.name)
        for path in (self.source/'native/snapshots').glob('*.json'):
            if Snapshot.load(path).id != path.stem:
                raise ValueError('Source native snapshot ID mismatch')
            shutil.copyfile(path,algorithm._run_dir/'snapshots'/path.name)
        algorithm._sequence = len(self.records)

        # The verified branches made no gate sample. Their only RNG operation
        # was one shuffle apiece; the integration branch already shuffled too.
        config = algorithm.config
        if (config.num_epochs != 1 or config.integration_epochs != 1
                or len(algorithm.benchmark.train) != config.batch_size):
            raise ValueError('Unexpected native batch structure at the cutoff')
        rng = random.Random(config.seed)
        for _ in range(len(self.modules)+1):
            batch = list(algorithm.benchmark.train)
            rng.shuffle(batch)
        branches = dict.fromkeys(self.modules,initial)
        candidate = await algorithm._propose(initial,'integration',self.prompt,batch,[],rng,branches=branches)
        current = candidate if candidate is not None else initial
        if not await algorithm._execution_gate(current,algorithm.benchmark.train,'final'):
            current = initial
        return algorithm._promote(initial,current,modules={name:snapshot.id for name,snapshot in branches.items()})
