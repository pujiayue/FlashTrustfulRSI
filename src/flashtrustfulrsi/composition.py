"""Trust protocols around pinned FlashRSI objects; native search and grading stay native.

The Python protocol accepts already prepared upstream collaborators. The bounded
development CLI supplies an isolated runtime for supplied-context MiniAgent and
Claude Code only. Discovery of other upstream adapters is not runtime validation.
"""

import argparse
import asyncio
from collections.abc import Mapping
import copy
from dataclasses import dataclass
import hashlib
import importlib
import json
import math
from pathlib import Path
import re
import types
import tempfile

from .provider import write_json


SCHEMA = 'flashtrustfulrsi.compose.v1'


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def native_inventory():
    """Return upstream registries directly, without a second adapter registry."""
    native = importlib.import_module('flashrsi.runner')
    return {key: {name: f'{cls.__module__}.{cls.__name__}' for name, cls in sorted(values.items())}
            for key, values in (('algorithms', native._ALGORITHMS),
                                ('benchmarks', native._BENCHMARKS), ('harnesses', native._AGENTS))}


def paired_metric(before, after, metric):
    """Pair native scores without converting errors or absent labels into zero."""
    def index(rows):
        result = {}
        for row in rows:
            key = row.task.task_id
            if key in result:
                raise ValueError('Duplicate task pair key')
            result[key] = row
        return result
    left, right = index(before), index(after)
    if not left or left.keys() != right.keys():
        raise ValueError('Nonempty aligned task pairs required')
    if any(row.status != 'finished' for row in (*left.values(), *right.values())):
        return {'status': 'execution_error', 'value': None, 'pairs': len(left)}
    values = []
    for key in left:
        pair = []
        for row in (left[key], right[key]):
            value = row.metrics.get(metric)
            if type(value) not in (int, float) or not math.isfinite(value):
                raise ValueError('Missing or nonfinite native metric')
            pair.append(value)
        values.append(pair)
    return {'status': 'ok', 'metric': metric, 'pairs': len(values),
            'before': sum(a for a, _ in values)/len(values),
            'after': sum(b for _, b in values)/len(values),
            'delta': sum(b-a for a, b in values)/len(values),
            'regressions': sum(b<a for a, b in values),
            'regression_rate': sum(b<a for a, b in values)/len(values)}


@dataclass(frozen=True)
class AuditSpec:
    name: str
    benchmark: Mapping


@dataclass(frozen=True)
class AuditContext:
    """A frozen audit view; it is never supplied to the native optimizer."""
    name: str
    benchmark: object
    before: tuple
    after: tuple
    snapshots: Mapping


class NativeUtilityEvaluator:
    """A metric plugin; changing this plugin does not change RSI search policy."""
    name = 'native-paired-utility'
    dimensions = ('D1',)
    stages = ('S4',)

    def evaluate(self, context):
        rows = (*context.before, *context.after)
        metrics = sorted(set.union(*(set(row.metrics) for row in rows)))
        if any(context.benchmark.primary_metric not in row.metrics for row in rows):
            raise ValueError('Native primary metric absent from audit evidence')
        results = {}
        for metric in metrics:
            results[metric] = (paired_metric(context.before, context.after, metric)
                if all(metric in row.metrics for row in rows) else
                {'status':'missing_metric', 'value':None,
                 'observed_trajectories':sum(metric in row.metrics for row in rows), 'expected_trajectories':len(rows)})
            results[metric]['same_snapshot_control'] = context.snapshots['initial']['id']==context.snapshots['selected']['id']
        return results


class SnapshotProvenanceEvaluator:
    """D5 engineering evidence; a valid snapshot hash is not a safety verdict."""
    name='snapshot-provenance'
    dimensions=('D5',)
    stages=('S3','S4')

    def __init__(self,directory):self.directory=Path(directory)

    def evaluate(self,context):
        from flashrsi.core import Snapshot
        versions={}
        for role,expected in context.snapshots.items():
            path=self.directory/(role+'.json');snapshot=Snapshot.load(path)
            if snapshot.id!=expected['id'] or digest(path)!=expected['sha256']:
                raise ValueError('Snapshot provenance differs from the sealed audit')
            versions[role]=snapshot
        before,after=versions['initial'],versions['selected']
        if before.root_id!=after.root_id:raise ValueError('Audited snapshots have different native lineage roots')
        names=set(before.files)|set(after.files)
        changed=[name for name in sorted(names) if before.files.get(name)!=after.files.get(name)]
        return {'status':'verified','same_snapshot':before.id==after.id,'root_id':before.root_id,
                'selected_parent_id':after.parent_id,'direct_parent_transition':after.parent_id==before.id,
                'changed_files':changed,'changed_components':sorted({(after.files.get(name) or before.files[name]).component for name in changed}),
                'complete_ancestry_claim':before.id==after.id or after.parent_id==before.id,
                'safety_verdict':None,'meaning':'Snapshot integrity and direct ancestry only; no behavioral safety or recursive-improvement conclusion.'}


def evaluator_plugins(names,directory):
    factories={'native-paired-utility':NativeUtilityEvaluator,'snapshot-provenance':lambda:SnapshotProvenanceEvaluator(directory)}
    if (not isinstance(names,list) or not names or len(names)!=len(set(names))
            or any(name not in factories for name in names)):
        raise ValueError('Explicit distinct passive evaluator names required')
    return tuple(factories[name]() for name in names)


class ComponentAuditEvaluator:
    """Expose an existing scoring component through an explicit evidence mapper.

    Selectors and memory/skill update components intentionally are not admitted
    as passive scoring plugins. Their invocation belongs to the corresponding
    native policy and has its own experimental condition.
    """
    SCORERS = frozenset({'seabench-monitor-metrics', 'agentbreeder-bootstrap',
                        'trusting-trust-static', 'agentpoison-react-metrics'})

    def __init__(self, component, *, root, evidence_mapper, dimensions, stages=('S4',)):
        if component not in self.SCORERS:
            raise ValueError('This component is not a passive audit scorer')
        self.name = component
        self.root = Path(root)
        self.evidence_mapper = evidence_mapper
        self.dimensions, self.stages = tuple(dimensions), tuple(stages)

    def evaluate(self, context):
        from .components import evaluate
        payload = self.evidence_mapper(context)
        if not isinstance(payload, dict):
            raise ValueError('An explicit native scorer evidence mapping is required')
        return evaluate(self.name, payload, self.root)


class TrustSession:
    """Observe native events, then audit frozen versions with prepared adapters.

    The native Algorithm owns every proposal and acceptance. This observer never
    changes a gate. Callers add environment bindings using upstream integrations.
    Factories, rather than live audit collaborators, keep heldout evaluation out
    of the optimizer's object graph.
    """

    def __init__(self, directory, *, evaluators=None):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=False)
        self.evaluators = tuple(evaluators) if evaluators is not None else (NativeUtilityEvaluator(),)
        if not self.evaluators or len({item.name for item in self.evaluators}) != len(self.evaluators):
            raise ValueError('Distinct evaluator plugin names required')

    async def evolve(self, algorithm, audits, target_factory, benchmark_factory):
        native_dir = self.directory/'native'
        for part in ('snapshots', 'trajectories', 'working'):
            (native_dir/part).mkdir(parents=True)
        algorithm._run_dir = native_dir
        initial = algorithm.target.snapshot
        initial.save(self.directory/'initial.json')
        events = []
        original = algorithm._record

        def observe(instance, kind, payload):
            result = original(kind, payload)
            event = {'sequence': len(events)+1, 'kind': kind}
            if kind == 'decision':
                event['decision'] = {key:payload[key] for key in
                    ('kind','parent_id','candidate_id','accepted') if key in payload}
            event['native_sequence'] = getattr(instance,'_sequence',None)
            events.append(event)
            write_json(self.directory/'events.json', events)
            return result

        algorithm._record = types.MethodType(observe, algorithm)
        try:
            selected = await algorithm.evolve()
        finally:
            algorithm._record = original
        return await self.audit(initial, selected, audits, target_factory, benchmark_factory, events=events)

    async def audit(self, initial, selected, audits, target_factory, benchmark_factory, *, events=(), cached_pairs=None):
        """Audit saved native versions with no Worker, Algorithm or new selection."""
        from flashrsi._records import _trajectory_record
        if not (self.directory/'initial.json').exists():
            initial.save(self.directory/'initial.json')
        selected.save(self.directory/'selected.json')
        frozen = {role: {'id': snapshot.id, 'sha256': digest(self.directory/f'{role}.json')}
                  for role, snapshot in (('initial', initial), ('selected', selected))}
        write_json(self.directory/'selection.json', {'snapshots': frozen, 'native_policy_unchanged': True})
        results = {}
        for audit in audits:
            benchmark = benchmark_factory(audit.benchmark)
            splits = benchmark.load()
            if not splits['test'] or splits['train'] or splits['dev']:
                raise ValueError('Audit benchmarks must contain test-only developer records')
            pair = {}
            for role, snapshot in (('initial', initial), ('selected', selected)):
                if cached_pairs is not None and (audit.name,role) in cached_pairs:
                    pair[role]=tuple(cached_pairs[audit.name,role])
                    if any(row.status!='finished' for row in pair[role]):
                        raise ValueError('Cached audit contains incomplete observations')
                    verified=benchmark.grade(pair[role])
                    if [dict(row.metrics) for row in verified]!=[dict(row.metrics) for row in pair[role]]:
                        raise ValueError('Cached audit native metrics changed')
                else:
                    target = target_factory(f'{audit.name}-{role}')
                    if target.snapshot.id != initial.id:
                        raise ValueError('Audit target did not reopen the same native seed root')
                    target.update(snapshot)
                    target.set_scope({name: 'r' for name in target.component_rules})
                    pair[role] = benchmark.grade(await target.rollout(splits['test'], snapshot=snapshot))
                if tuple(row.task for row in pair[role]) != tuple(splits['test']):
                    raise ValueError('Native grading changed task membership or ordering')
                write_json(self.directory/f'{audit.name}-{role}.json',
                           [_trajectory_record(row) for row in pair[role]])
                if any(digest(self.directory/f'{key}.json') != meta['sha256'] for key, meta in frozen.items()):
                    raise ValueError('Independent audit changed a frozen snapshot')
            if any(row.status!='finished' for rows in pair.values() for row in rows):
                raise ValueError('Incomplete audit trajectories; see archived native rows')
            context = AuditContext(audit.name, benchmark, tuple(pair['initial']), tuple(pair['selected']), frozen)
            plugin_results = {item.name: item.evaluate(context) for item in self.evaluators}
            write_json(self.directory/f'{audit.name}-evaluators.json', plugin_results)
            results[audit.name] = plugin_results
        write_json(self.directory/'audit.json', results)
        return {'snapshots': frozen, 'audits': results, 'events': len(events)}


def load_spec(path):
    """Validate an explicit developer data manifest; no data downloader or API."""
    spec = json.loads(Path(path).read_text())
    if (spec.get('schema') != SCHEMA or spec.get('scope') != 'development_only'
            or spec.get('model') != 'deepseek-flash' or spec.get('formal_data_used') is not False):
        raise ValueError('Expected an official deepseek-flash developer-only composition')
    inventory = native_inventory()
    if spec['harness'] not in inventory['harnesses'] or spec['algorithm']['name'] not in inventory['algorithms']:
        raise ValueError('Unknown native collaborator')
    names = [entry['name'] for entry in spec['audits']]
    if (not names or len(names) != len(set(names))
            or any(not re.fullmatch(r'[a-z][a-z0-9-]{0,8}', name) for name in names)):
        raise ValueError('Distinct short audit names required')
    all_paths = set()
    for bench in (spec['source'], *(entry['benchmark'] for entry in spec['audits'])):
        if bench['name'] not in inventory['benchmarks']:
            raise ValueError('Unknown native benchmark')
        for value in bench['data_path'].values():
            path = str(Path(value).resolve())
            if path not in spec['data_sha256'] or digest(path) != spec['data_sha256'][path]:
                raise ValueError('Frozen developer data hash mismatch')
            all_paths.add(path)
    if all_paths != set(spec['data_sha256']):
        raise ValueError('Data hash manifest contains unused inputs')
    if set(spec['source']['data_path']) - {'train', 'dev'} or 'train' not in spec['source']['data_path']:
        raise ValueError('Optimizer data must be train/dev only')
    if any(set(entry['benchmark']['data_path']) != {'test'} for entry in spec['audits']):
        raise ValueError('Frozen audits must be test-role developer slices')
    return spec


def native_preflight(spec,benchmark,budget,worker_spec):
    """Construct native collaborators in a disposable seed before any provider exists."""
    from flashrsi import runner as native
    from flashrsi.agents.llm import LLM
    from scripts._searchqa_experiment_support import create_seed
    if spec['harness'] not in ('miniagent','claude_code'):
        return {'status':'inherited_adapter_requires_prepared_runtime','harness':spec['harness'],'model_requests':0}
    llm={'model_name':'deepseek-flash','api_key':'no-secret','base_url':'http://model-gateway/v1',
         'max_tokens':budget.per_call_output}
    with tempfile.TemporaryDirectory(prefix='ftrsi-compose-preflight-') as temp:
        seed=create_seed(spec['harness'],Path(temp)/'seed')
        target=native._AGENTS[spec['harness']]({'name':spec['harness'],'source_dir':str(seed),'llm':llm})
        async def preflight_only(*args):raise RuntimeError('Preflight never executes tasks')
        target._env_runner=preflight_only
        if worker_spec['type']=='none':
            return {'status':'native_frozen_evaluation_only','worker_instantiated':False,'model_requests':0}
        if worker_spec['type']=='llm':worker=LLM({'name':'llm','type':'llm','llm':llm})
        else:
            seed=create_seed('miniagent',Path(temp)/'worker-seed')
            worker=native._AGENTS['miniagent']({'name':'miniagent','type':'agent','source_dir':str(seed),'llm':llm})
        algorithm=native._ALGORITHMS[spec['algorithm']['name']](spec['algorithm'],target=target,worker=worker,benchmark=benchmark)
        return {'status':'native_constructor_verified','algorithm':algorithm.name,'worker':worker_spec['type'],
                'evolve_scope':sorted(algorithm._evolve),'model_requests':0}


async def execute(args):
    from flashrsi import runner as native
    from flashrsi.agents.llm import LLM
    from .deepseek_debug import DebugBudget, DeepSeekProvider, MODEL
    from .deepseek_anthropic import DeepSeekAnthropicProvider
    from .native_container import bind_miniagent
    from .claude_container import bind_claude_code
    from .native_llm import bind_llm
    from .searchqa_deepseek_dev import SearchQAFlashTransport
    from .searchqa_formal import COMMIT

    spec = load_spec(args.config)
    evaluator_plugins(spec.get('evaluators',['native-paired-utility']),Path('.'))
    if spec.get('cached_audits') and not spec.get('frozen'):
        raise ValueError('Cached audit observations require sealed frozen evaluation')
    source_bench = native._BENCHMARKS[spec['source']['name']](spec['source'])
    source_splits = source_bench.load()
    if not source_splits['train'] or source_splits['test']:
        raise ValueError('Native optimizer must load nonempty train data and no test tasks')
    source_rows = tuple(source_splits['train'])+tuple(source_splits['dev'])
    if len({task.task_id for task in source_rows}) != len(source_rows):
        raise ValueError('Duplicate source task IDs')
    source_questions = {task.metadata.get('question_checksum', task.question) for task in source_rows}
    membership = {'source': {key:[task.task_id for task in rows] for key,rows in source_splits.items()}}
    for audit in spec['audits']:
        bench = native._BENCHMARKS[audit['benchmark']['name']](audit['benchmark'])
        splits = bench.load()
        if not splits['test'] or splits['train'] or splits['dev']:
            raise ValueError('Native audit loader must load only heldout developer tasks')
        if any(task.metadata.get('question_checksum', task.question) in source_questions for task in splits['test']):
            raise ValueError('An audit question overlaps the optimizer source pool')
        membership[audit['name']] = [task.task_id for task in splits['test']]
    budget = DebugBudget(**spec['budget'])
    from .searchqa_formal import TEXT_METHODS
    frozen_only = args.action=='evaluate' or (args.action=='plan' and 'frozen' in spec)
    worker_spec = ({'type':'none'} if frozen_only else spec.get('worker',
                    {'type':'llm' if spec['algorithm']['name'] in TEXT_METHODS else 'miniagent'}))
    if (worker_spec.get('type') not in (('none',) if frozen_only else ('llm','miniagent')) or
            worker_spec.keys()-{'type','image','max_turns','timeout','instruction'}):
        raise ValueError('Development workers must be native LLM or isolated MiniAgent')
    if worker_spec['type']=='miniagent' and not re.fullmatch(r'sha256:[0-9a-f]{64}',worker_spec.get('image','')):
        raise ValueError('The directory-editing worker requires its own immutable MiniAgent image')
    if (type(worker_spec.get('max_turns',22)) is not int or not 1<=worker_spec.get('max_turns',22)<=128
            or type(worker_spec.get('timeout',1200)) is not int or not 1<=worker_spec.get('timeout',1200)<=3600):
        raise ValueError('Invalid development worker turn or runtime cap')
    if not isinstance(worker_spec.get('instruction',''),str) or len(worker_spec.get('instruction',''))>8192:
        raise ValueError('Worker instruction must be bounded text')
    checked = native_preflight(spec,source_bench,budget,worker_spec)
    plan = {**spec, 'job_id':args.job_id, 'runs_formal_eval': False, 'upstream_inventory_counts':
            {key: len(value) for key, value in native_inventory().items()},
            'input_membership':membership,
            'budget_scope':'per transport; Chat and Messages have separate persistent ceilings',
            'total_request_ceiling':budget.requests*(2 if spec['harness']=='claude_code' else 1),
            'native_runner_sha256': digest(native.__file__), 'adapter_sha256': digest(__file__),
            'resolved_worker':worker_spec,
            'native_preflight':checked,
            'execution_scope': 'supplied-context MiniAgent/ClaudeCode; other environments use prepared Python collaborators'}
    root = Path(args.root).resolve()
    upstream = root/f'upstream/RUC-NLPIR-FlashRSI-{COMMIT[:7]}'
    if Path(native.__file__).resolve() != upstream/'flashrsi/runner.py':
        raise ValueError('The composition requires the project-pinned upstream checkout')
    if args.action == 'plan':
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return
    if (spec['harness'] not in ('miniagent', 'claude_code') or spec['source'].get('mode') != 'context'
            or any(row['benchmark'].get('mode') != 'context' for row in spec['audits'])):
        raise ValueError('Use native prepared collaborators for an environment-aware Python integration')
    if not re.fullmatch(r'sha256:[0-9a-f]{64}', args.image or ''):
        raise ValueError('An immutable local runtime image is required')
    directory = root/'runs'/args.job_id
    directory.mkdir(parents=True, mode=0o700)
    write_json(directory/'manifest.json', {**plan, 'image': args.image})
    write_json(directory/'status.json', {'status': 'running', 'scope': 'development_only'})
    try:
        from scripts._searchqa_experiment_support import create_seed
        frozen = spec.get('frozen') if args.action=='evaluate' else None
        if args.action=='evaluate' and not frozen:
            raise ValueError('Frozen evaluation requires explicit seed and snapshot hashes')
        seed = Path(frozen['seed_dir']) if frozen else create_seed(spec['harness'], directory/'seed')
        if spec.get('controlled_instruction') and not frozen:
            instruction = 'CLAUDE.md' if spec['harness']=='claude_code' else 'prompts/system.md'
            with (seed/instruction).open('a') as stream:
                stream.write('\n'+spec['controlled_instruction']+'\n')
        provider = DeepSeekProvider(directory/'provider-chat', args.job_id+'-chat', budget, timeout=180)
        messages_provider = (DeepSeekAnthropicProvider(directory/'provider-messages', args.job_id+'-messages',
                             budget, timeout=180) if spec['harness']=='claude_code' else None)
        wire = SearchQAFlashTransport(provider)
        llm = {'model_name': MODEL, 'api_key': 'no-secret', 'base_url': 'http://model-gateway/v1',
               'max_tokens': budget.per_call_output}

        async def context(agent, task):
            if task.env != 'supplied_context':
                raise ValueError('The development runtime only executes native supplied-context tasks')
            return await agent.run(task.question)

        def target(role):
            config = {'name': spec['harness'], 'source_dir': str(seed), 'max_turns': 8, 'timeout': 600,
                      'llm': {**llm, 'llm_max_retries': 1}}
            cls = native._AGENTS[spec['harness']]
            if spec['harness']=='claude_code':
                config['tools'] = ['Read', 'Glob', 'Grep', 'Skill']
                config['allowed_tools'] = ['Read', 'Glob', 'Grep', 'Skill']
                config['llm'] = {key: value for key, value in llm.items() if key!='llm_max_retries'}
                config['llm']['base_url'] = 'http://model-gateway'
                agent = bind_claude_code(cls(config), image=args.image, provider=messages_provider,
                         artifact_dir=directory/'invocations', upstream=upstream, role=role)
            else:
                agent = bind_miniagent(cls(config), image=args.image, provider=wire,
                                      artifact_dir=directory/'invocations', role=role)
            agent._env_runner = context
            return agent

        def benchmark(config):
            return native._BENCHMARKS[config['name']](config)

        audits = [AuditSpec(row['name'], row['benchmark']) for row in spec['audits']]
        session = TrustSession(directory/'trust',evaluators=evaluator_plugins(
                    spec.get('evaluators',['native-paired-utility']),directory/'trust'))
        if frozen:
            from flashrsi.core import Snapshot
            snapshots = {}
            for role in ('initial', 'selected'):
                source = Path(frozen[role]['path'])
                if digest(source)!=frozen[role]['sha256']:
                    raise ValueError('Frozen source snapshot hash mismatch')
                snapshots[role] = Snapshot.load(source)
            from .composition_recovery import read_cache
            cached=read_cache(spec,audits,benchmark)
            write_json(directory/'audit-reuse.json',{'reused_roles':spec.get('cached_audits',[]),
                        'source':spec.get('recovery'),'new_inference_roles':spec.get('recovery',{}).get('remaining_roles')})
            result = await session.audit(snapshots['initial'], snapshots['selected'], audits, target, benchmark,cached_pairs=cached)
        else:
            cls = native._ALGORITHMS[spec['algorithm']['name']]
            if worker_spec['type']=='llm':
                if spec['algorithm']['name'] not in TEXT_METHODS:
                    raise ValueError('This native algorithm needs an editing worker')
                worker = bind_llm(LLM({'name': 'llm', 'type': 'llm', 'timeout': 180, 'max_retries': 0, 'llm': llm}), wire)
            else:
                worker_seed = create_seed('miniagent', directory/'worker-seed')
                if worker_spec.get('instruction'):
                    with (worker_seed/'prompts/system.md').open('a') as stream:
                        stream.write('\n'+worker_spec['instruction']+'\n')
                settings = {'name':'miniagent','type':'agent','source_dir':str(worker_seed),
                            'max_turns':worker_spec.get('max_turns',22),'timeout':worker_spec.get('timeout',1200),
                            'llm':{**llm,'llm_max_retries':1}}
                worker = bind_miniagent(native._AGENTS['miniagent'](settings),image=worker_spec['image'],
                         provider=wire,artifact_dir=directory/'invocations',role='worker')
            algorithm = cls(spec['algorithm'], target=target('source'), worker=worker, benchmark=benchmark(spec['source']))
            result = await session.evolve(algorithm, audits, target, benchmark)
        ledgers = {'chat': provider.summary()}
        if messages_provider:
            ledgers['messages'] = messages_provider.summary()
        if any(row['state']!='success' for ledger in ledgers.values() for row in ledger['calls']):
            raise ValueError('Incomplete official inference receipts')
        write_json(directory/'result.json', {'status': 'completed', 'runs_formal_eval': False,
                   'result': result, 'ledgers': ledgers})
        write_json(directory/'status.json', {'status': 'completed', 'scope': 'development_only'})
    except Exception as exc:
        write_json(directory/'status.json', {'status': 'failed', 'error_type': type(exc).__name__, 'error': str(exc)})
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('inventory', 'plan', 'run', 'evaluate','inspect-recovery','recover-audit'))
    parser.add_argument('--source-run',type=Path)
    parser.add_argument('--config', type=Path)
    parser.add_argument('--root', default='.')
    parser.add_argument('--job-id')
    parser.add_argument('--image')
    parser.add_argument('--evaluators',help='Passive audit plugins for recovered observations; does not change native selection')
    args = parser.parse_args(argv)
    if args.action == 'inventory':
        print(json.dumps({'scope': 'upstream_available_not_all_runtime_verified', **native_inventory()}, indent=2))
        return
    if args.evaluators and args.action not in ('inspect-recovery','recover-audit'):
        parser.error('--evaluators is a recovery override; for a new run set evaluators in its hashed configuration')
    if args.action in ('inspect-recovery','recover-audit'):
        if not args.source_run:parser.error('--source-run required')
        from .composition_recovery import prepare
        spec=prepare(args.source_run)
        if args.evaluators:
            spec['evaluators']=args.evaluators.split(',')
            evaluator_plugins(spec['evaluators'],Path('.'))
        if args.action=='inspect-recovery':
            print(json.dumps({'recovery':spec['recovery'],'cached_audits':spec['cached_audits']},indent=2));return
        if args.config:parser.error('Recovery derives its configuration from the sealed source; do not override --config')
        args.image=args.image or spec['image']
        if args.image!=spec['image']:parser.error('Recovery must retain the source target runtime image')
        with tempfile.TemporaryDirectory(prefix='ftrsi-audit-recovery-') as temp:
            config=Path(temp)/'config.json';write_json(config,spec)
            main(['evaluate','--config',str(config),'--root',args.root,'--job-id',args.job_id or '', '--image',args.image])
        return
    if not args.config:
        parser.error('--config required')
    if args.action in ('run', 'evaluate') and not re.fullmatch(r'dev-dsflash-compose-[a-z0-9-]{1,32}', args.job_id or ''):
        parser.error('A development-only job ID is required')
    asyncio.run(execute(args))


if __name__ == '__main__':
    main()
