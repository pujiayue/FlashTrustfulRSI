"""Run the native AgentBreeder population loop with sandboxed scoring."""
import argparse
import asyncio
import hashlib
import importlib
import json
import math
from pathlib import Path
import re
from shutil import copyfile
import sqlite3
import sys
from types import ModuleType, SimpleNamespace

from .agentbreeder_live import COMMIT, digest, run_candidate
from .native_spool import NativeSpoolProvider
from .provider import write_json
from .transport import MODEL
from .deepseek_debug import CHAT_URL, DebugBudget, DeepSeekProvider, MODEL as DEBUG_MODEL, ledger_status


CALLS_PER_EPISODE = 12
DESCRIPTOR_DIM = 128


class LocalDescriptor:
    """Deterministic feature hashing for code when an embedding API is out of scope."""

    def generate(self, scaffold):
        vector = [0.0] * DESCRIPTOR_DIM
        for token in re.findall(r'\w+|[^\w\s]', scaffold.scaffold_name + '\n' + scaffold.scaffold_code):
            hashed = hashlib.sha256(token.encode()).digest()
            vector[hashed[0] % DESCRIPTOR_DIM] += 1 if hashed[1] & 1 else -1
        length = math.sqrt(sum(n * n for n in vector)) or 1
        return [n / length for n in vector]


class FlashPopulationTransport:
    def __init__(self, provider):
        self.provider = provider

    def complete_sync(self, messages, *, max_tokens, request_id, **options):
        return self.invoke('chat', {'model':MODEL,'messages':messages,'max_tokens':max_tokens,**options}, request_id)

    def invoke(self, protocol, body, request_id):
        if protocol!='chat' or body.get('model')!=MODEL:
            raise ValueError('Unexpected isolated AgentBreeder payload')
        payload = {**body, 'model': DEBUG_MODEL}
        return self.provider.invoke(protocol, payload, request_id)


def execute(root, args, data, source, dest, prior=None):
    mode = getattr(args,'selection_mode','blue')
    debug = getattr(args,'deepseek_debug',False)
    provider = DeepSeekProvider(dest/'provider',args.job_id,args.debug_budget,timeout=180) if debug else None
    spool = FlashPopulationTransport(provider) if debug else NativeSpoolProvider(root / 'runs/model-spool', args.job_id, timeout=300)
    transport_error = []
    call_sequence = 0
    debug_sequence = 0
    ineligible = set()
    resume_generation = getattr(args, 'resume_generation_from', None)
    finalize = getattr(args, 'finalize_recluster_from', None)

    async def structured(messages, response_format, model=None, temperature=.5, retry=0):
        nonlocal call_sequence
        if transport_error:
            raise transport_error[0]
        note = 'Return a JSON object with fields: '+json.dumps(response_format)
        if resume_generation and 'code' in response_format:
            note += ' Keep the scaffold short. Encode code as one JSON string with escaped newlines and quotes.'
        for attempt in range(2 if resume_generation and 'code' in response_format else 1):
            call_sequence += 1
            if attempt:
                note += ' The prior response was invalid JSON. Return a complete, shorter JSON object.'
            try:
                response = await asyncio.to_thread(
                    spool.complete_sync, [*messages, {'role':'user', 'content':note}],
                    max_tokens=4096, request_id=f'native-structured-{call_sequence:04d}',
                    temperature=temperature, response_format={'type':'json_object'},
                )
            except Exception as exc:
                transport_error.append(exc)
                raise
            try:
                value = json.loads(response['choices'][0]['message']['content'])
                break
            except json.JSONDecodeError:
                if not resume_generation or 'code' not in response_format or attempt:
                    raise
        if not isinstance(value, dict) or any(not isinstance(value.get(k), str) for k in response_format):
            raise ValueError('Malformed AgentBreeder structured response')
        if 'is_safe' in response_format and value['is_safe'].strip().lower() not in {'yes', 'no'}:
            raise ValueError('Malformed SALAD judge label')
        return value

    # Upstream imports eagerly construct vendor clients. Only the exact-model
    # spool is registered, including for any incidental trusted upstream call.
    api = ModuleType('api')
    api.get_json_completion = structured
    sys.modules['api'] = api
    sys.path.insert(0, str(source))
    upstream = importlib.import_module('main')
    native_init = importlib.import_module('base.initialize')
    native_session = importlib.import_module('base.session')
    native_discover = importlib.import_module('discover.discover')
    native_evolve = importlib.import_module('discover.evolve')
    native_salad = importlib.import_module('evals.benchmarks.salad_data')
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    db_path = dest / 'population.sqlite3'
    if prior:
        with sqlite3.connect(f'file:{prior / "population.sqlite3"}?mode=ro', uri=True) as before, sqlite3.connect(db_path) as after:
            before.backup(after)
    engine = create_engine(f'sqlite:///{db_path}')
    native_session.engine = engine
    # The released prompt builder uses an old column name.
    upstream.Scaffold.scaffold_agent_code = property(lambda scaffold: scaffold.scaffold_code)
    native_init.Descriptor = native_discover.Descriptor = LocalDescriptor
    native_init_run = upstream.initialize_population_id

    def initialize(native_args):
        if prior:
            with Session(engine) as session:
                population = session.query(upstream.Population).one()
                if len(population.scaffolds) != 7 or len(population.generations) != 1:
                    raise ValueError('Seed-only recovery requires exactly one initialized population and no offspring')
                for scaffold in population.scaffolds:
                    filename = f'validation-{scaffold.scaffold_id}.json'
                    path = prior / filename
                    validation = json.loads(path.read_text())
                    if (scaffold.scaffold_first_parent_id is not None or
                        any(row['status']!='finished' for row in validation.values()) or
                        scaffold.scaffold_capability_ci_median!=validation['mmlu']['score'] or
                        scaffold.scaffold_safety_ci_median!=validation['salad']['score']):
                        raise ValueError('Prior seed validation and database do not match')
                    copyfile(path,dest/filename)
                identity = population.population_id
        else:
            identity = native_init_run(native_args)
        native_args.population_id = identity
        return identity

    upstream.initialize_population_id = initialize

    def check_fatal():
        if transport_error:
            raise RuntimeError('AgentBreeder transport failed') from transport_error[0]

    async def isolated_debug(evolver, messages, proposal, response_format):
        nonlocal debug_sequence
        check_fatal()
        debug_sequence += 1
        label = f'debug-{debug_sequence:04d}'
        case = data['validation']['mmlu']
        try:
            result = await asyncio.to_thread(
                run_candidate, spool, args.image, source, case, proposal['code'], label,
                dest / (label+'-process.json'), seed=args.seed, max_calls=CALLS_PER_EPISODE, timeout=900,
            )
        except Exception as exc:
            transport_error.append(exc)
            raise
        write_json(dest / (label+'.json'), {'candidate': proposal, 'result': result})
        return proposal if result['status'] == 'finished' else {**proposal, 'code': None}

    native_evolve.Evolve._debug = isolated_debug
    original_elites = upstream.Population.pareto_elites.fget
    upstream.Population.pareto_elites = property(
        lambda population: [row for row in original_elites(population) if row.scaffold_id not in ineligible]
    )
    if mode=='ablation':
        original_capability = upstream.Population.elites.fget
        upstream.Population.elites = property(
            lambda population: [row for row in original_capability(population) if row.scaffold_id not in ineligible]
        )
    original_generation = native_discover.Discover.run_generation
    generations = 2 if finalize else 1 if resume_generation else 0

    async def generation(discover, session):
        nonlocal generations
        result = await original_generation(discover, session)
        check_fatal()
        generations += 1
        write_json(dest / f'generation-{generations}.json', {'attempts': result})
        if not any(row and row['scaffold_code'] for row in result):
            raise RuntimeError('Native generation produced no executable offspring')
        return result

    native_discover.Discover.run_generation = generation

    def score(scaffold, split):
        rows = {}
        for kind, case in data[split].items():
            label = f'{split}-{scaffold.scaffold_id}-{kind}'
            result = run_candidate(spool, args.image, source, case, scaffold.scaffold_code,
                                   label, dest / (label+'-process.json'),
                                   seed=args.seed, max_calls=CALLS_PER_EPISODE, timeout=900)
            if result['status'] == 'finished':
                if kind == 'mmlu':
                    result['score'] = int(result['output'].strip().upper() == case['answer'])
                else:
                    state = SimpleNamespace(metadata={'baseq': case['baseq']},
                                            output=SimpleNamespace(completion=result['output']))
                    scorer = native_salad.SaladData.llm_safe()
                    verdict = asyncio.run(scorer(state, None))
                    check_fatal()
                    if isinstance(verdict.explanation, Exception):
                        raise RuntimeError('SALAD judge produced an invalid score') from verdict.explanation
                    result.update(score=verdict.value, explanation=verdict.explanation)
            rows[kind] = {'case_id': case['id'], **result}
        write_json(dest / f'{split}-{scaffold.scaffold_id}.json', rows)
        return rows

    class Validator:
        def __init__(self, native_args, split='validation'):
            self.benchmark = SimpleNamespace(dataset=[data[split]['mmlu']])
            self.split = split

        def validate(self, scaffolds, log_d='logs'):
            for scaffold in scaffolds:
                rows = score(scaffold, self.split)
                if not all(row['status'] == 'finished' for row in rows.values()):
                    ineligible.add(scaffold.scaffold_id)
                # Explicitly separate algorithm-facing penalties from measured outcomes.
                accepted = scaffold.scaffold_id not in ineligible
                capability = rows['mmlu']['score'] if accepted else 0
                safety = rows['salad']['score'] if accepted else 0
                scaffold.update(scaffold_fitness=capability, scaffold_capability_ci_median=capability,
                                scaffold_safety_ci_median=safety)

    upstream.Validator = native_init.Validator = native_discover.Validator = Validator
    population_id = None
    if resume_generation or finalize:
        with Session(engine) as session:
            population = session.query(upstream.Population).one()
            expected_records = 4 if finalize else 2
            if len(population.generations)!=expected_records or len(population.scaffolds)!=7+generations:
                raise ValueError('Committed population does not match the reviewed cutoff')
            for scaffold in population.scaffolds:
                name = f'validation-{scaffold.scaffold_id}.json'
                rows = json.loads((prior/name).read_text())
                if (any(row['status']!='finished' for row in rows.values())
                        or scaffold.scaffold_capability_ci_median!=rows['mmlu']['score']
                        or scaffold.scaffold_safety_ci_median!=rows['salad']['score']):
                    raise ValueError('Committed population and validation differ')
                copyfile(prior/name, dest/name)
            population_id = population.population_id
        for index in range(1, generations+1):
            name = f'generation-{index}.json'
            copyfile(prior/name, dest/name)
    native_args = SimpleNamespace(random_seed=args.seed, n_generation=args.generations-generations, n_mutations=args.children,
                                  n_evals=1, debug_max=1, mode=mode, model=MODEL, population_id=None,
                                  benchmark='mmlu', current_dir=str(source), log_timestamp=args.job_id)
    if population_id:
        native_args.population_id = population_id
    identity = population_id if finalize else upstream.main(native_args, population_id=population_id)
    check_fatal()
    with Session(engine) as session:
        population = session.query(upstream.Population).filter_by(population_id=identity).one()
        extra_recluster = int(bool(resume_generation or finalize))
        if len(population.generations) != args.generations + 1 + extra_recluster or generations != args.generations:
            raise RuntimeError('Native population stopped before all generations completed')
        eligible = population.pareto_elites if mode=='blue' else population.elites
        if not eligible:
            raise RuntimeError('Native population contains no eligible scaffold')
        selected = max(eligible, key=lambda s: ((s.scaffold_safety_ci_median if mode=='blue' else 0),
                                               s.scaffold_capability_ci_median, -s.scaffold_timestamp.timestamp()))
        heldout = score(selected, 'test')
        if any(row['status'] != 'finished' for row in heldout.values()):
            raise RuntimeError('Selected scaffold failed held-out execution')
        lineage = [{'id': s.scaffold_id, 'name': s.scaffold_name, 'first_parent': s.scaffold_first_parent_id,
                    'second_parent': s.scaffold_second_parent_id, 'cluster': s.cluster_id,
                    'eligible': s.scaffold_id not in ineligible,
                    'capability': s.scaffold_capability_ci_median if s.scaffold_id not in ineligible else None,
                    'safety': s.scaffold_safety_ci_median if s.scaffold_id not in ineligible else None}
                   for s in population.scaffolds]
        result = {'status': 'completed', 'original_population_main': True, 'generations': generations,
                'population_generation_records': len(population.generations), 'recovery_recluster_records': extra_recluster,
                'population_size': len(lineage), 'selected': selected.scaffold_id, 'lineage': lineage,
                'heldout': heldout}
        result['eligible_pareto_ids' if mode=='blue' else 'eligible_capability_ids'] = [s.scaffold_id for s in eligible]
        if debug:
            result.update(selection_mode=mode,model=DEBUG_MODEL,ledger=provider.summary())
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', default='.')
    parser.add_argument('--job-id', required=True)
    parser.add_argument('--data', required=True, type=Path)
    parser.add_argument('--image', required=True)
    parser.add_argument('--seed', default=42, type=int)
    parser.add_argument('--generations', default=2, type=int, choices=range(1, 11))
    parser.add_argument('--children', default=2, type=int, choices=range(1, 6))
    parser.add_argument('--reuse-seeds-from')
    parser.add_argument('--resume-generation-from')
    parser.add_argument('--finalize-recluster-from')
    parser.add_argument('--selection-mode',choices=('blue','ablation'),default='blue')
    parser.add_argument('--deepseek-debug',action='store_true')
    parser.add_argument('--debug-requests', type=int, choices=(120, 180, 240))
    parser.add_argument('--plan-only', action='store_true')
    args = parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', args.job_id):
        parser.error('Invalid job identity')
    if args.deepseek_debug and not args.job_id.startswith('dev-dsflash-breeder-'):
        parser.error('DeepSeek debug requires a separate development identity')
    if args.debug_requests is not None and not args.deepseek_debug:
        parser.error('--debug-requests requires --deepseek-debug')
    if (args.resume_generation_from or args.finalize_recluster_from) and (
            not args.deepseek_debug or args.reuse_seeds_from or args.generations!=2 or args.children!=1
            or args.resume_generation_from and args.finalize_recluster_from):
        parser.error('Generation recovery requires a separate two-generation DeepSeek job with one child per generation')
    root = Path(args.root).resolve()
    source = root / 'external/research/E05/src'
    data = json.loads(args.data.read_text())
    if any(set(data[split]) != {'mmlu', 'salad'} for split in ('validation', 'test')):
        raise ValueError('Expected frozen MMLU/SALAD pairs')
    if any(data['validation'][kind]['id'] == data['test'][kind]['id'] for kind in ('mmlu', 'salad')):
        raise ValueError('Validation and held-out cases must be distinct')
    maximum = (8 + args.generations * args.children) * (2 * CALLS_PER_EPISODE + 1)
    maximum += args.generations * args.children * 3 * (3 + CALLS_PER_EPISODE)
    plan = {
        'job_id': args.job_id, 'scope': 'native_population_loop_with_adapted_evaluation', 'model': MODEL,
        'commit': COMMIT, 'source_sha256': {str(p.relative_to(source)): digest(p) for p in sorted(source.rglob('*.py'))},
        'image': args.image, 'data_sha256': digest(args.data), 'seed': args.seed, 'generations': args.generations,
        'children': args.children, 'seed_population_size': 7, 'max_requests': maximum,
        'descriptor': 'deterministic signed feature hashing; no external embedding model',
        'validation': 'isolated original scaffold runtime, exact MMLU and original SALAD scorer; no CI from one case',
        'debug': 'one isolated attempt per native proposal; rejected code can trigger original bounded resampling',
        'episode_timeout_seconds': 900,
        'selection': 'native cluster Pareto; eligible safety-first, capability then oldest',
        'adapter_sha256': digest(Path(__file__)),
        'scaffold_adapter_sha256': digest(Path(__file__).with_name('agentbreeder_live.py')),
    }
    if args.selection_mode=='ablation':
        plan['selection']='native ablation cluster capability elites; highest capability then oldest'
    if args.selection_mode!='blue' or args.deepseek_debug:
        plan['selection_mode']=args.selection_mode
    if args.deepseek_debug:
        request_cap = args.debug_requests or 120
        args.debug_budget=DebugBudget(requests=request_cap,input_tokens=8_000_000,
                                      output_tokens=request_cap*4096,per_call_output=4096)
        plan.update(model=DEBUG_MODEL, scope='development_only_native_population', runs_formal_eval=False,
                    max_requests=args.debug_budget.requests, provider_budget=args.debug_budget.__dict__)
    prior = None
    if args.finalize_recluster_from:
        identity = args.finalize_recluster_from
        if not re.fullmatch(r'dev-dsflash-breeder-[A-Za-z0-9_-]{1,55}',identity) or identity==args.job_id:
            raise ValueError('Invalid population finalization source')
        prior = root/'runs'/identity
        status = json.loads((prior/'status.json').read_text())
        old = json.loads((prior/'manifest.json').read_text())
        previous = ledger_status(prior/'provider')
        paths = [prior/f'generation-{index}.json' for index in (1,2)]
        validation = [p for p in sorted(prior.glob('validation-*.json')) if not p.name.endswith('-process.json')]
        if (status.get('error')!='Native population stopped before all generations completed'
                or not old.get('generation_resume') or any(old[key]!=plan[key] for key in
                    ('scope','model','commit','source_sha256','image','data_sha256','seed','selection_mode','generations','children','descriptor'))
                or previous['model']!=DEBUG_MODEL or previous['endpoint']!=CHAT_URL or previous['usage_unknown_calls']
                or any(row['state']!='success' for row in previous['calls']) or list(prior.glob('test-*.json'))
                or len(validation)!=9 or any(not any(row and row.get('scaffold_code') for row in
                    json.loads(path.read_text())['attempts']) for path in paths)):
            raise ValueError('Population is not a settled validation-complete cutoff')
        plan['population_finalize'] = {'source_job':identity,'db_sha256':digest(prior/'population.sqlite3'),
            'generation_sha256':{path.name:digest(path) for path in paths},
            'validation_sha256':{p.name:digest(p) for p in validation},'prior_requests':previous['requests'],
            'scope':'reuse both committed mutation generations and one extra native reload recluster; select and test only'}
    if args.resume_generation_from:
        identity = args.resume_generation_from
        if not re.fullmatch(r'dev-dsflash-breeder-[A-Za-z0-9_-]{1,55}',identity) or identity==args.job_id:
            raise ValueError('Invalid generation recovery source')
        prior = root/'runs'/identity
        status = json.loads((prior/'status.json').read_text())
        old = json.loads((prior/'manifest.json').read_text())
        old_ledger = ledger_status(prior/'provider')
        validation = [p for p in sorted(prior.glob('validation-*.json')) if not p.name.endswith('-process.json')]
        if (status.get('error')!='Native generation produced no executable offspring'
                or any(old[key]!=plan[key] for key in ('scope','model','commit','source_sha256','image','data_sha256',
                                                      'seed','selection_mode','generations','children','descriptor'))
                or old_ledger['endpoint']!=CHAT_URL or old_ledger['model']!=DEBUG_MODEL
                or old_ledger['usage_unknown_calls'] or any(row['state']!='success' for row in old_ledger['calls'])
                or len(validation)!=8 or json.loads((prior/'generation-2.json').read_text())['attempts']!=[None]):
            raise ValueError('Source is not a settled one-generation cutoff')
        plan['generation_resume'] = {'source_job':identity,'db_sha256':digest(prior/'population.sqlite3'),
                                     'generation_sha256':digest(prior/'generation-1.json'),
                                     'prior_requests':old_ledger['requests'],
                                     'validation_sha256':{p.name:digest(p) for p in validation},
                                     'remaining_generation':2,'structured_code_retry_limit':1}
    if args.reuse_seeds_from:
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',args.reuse_seeds_from) or args.reuse_seeds_from==args.job_id:
            raise ValueError('Invalid source job')
        prior = root / 'runs' / args.reuse_seeds_from
        status = json.loads((prior/'status.json').read_text())
        old = json.loads((prior/'manifest.json').read_text())
        if (status['status']!='failed' or old.get('selection_mode','blue')!=args.selection_mode
                or any(old.get(k)!=plan[k] for k in ('scope','model','commit','source_sha256','image','data_sha256','seed','descriptor'))):
            raise ValueError('Seed recovery identity changed')
        files = [p for p in sorted(prior.glob('validation-*.json')) if not p.name.endswith('-process.json')]
        if len(files)!=7:
            raise ValueError('Only seven fully scored seeds may be recovered')
        plan['seed_reuse'] = {'source_job':prior.name,'db_sha256':digest(prior/'population.sqlite3'),
                              'validation_sha256':{p.name:digest(p) for p in files},
                              'scope':'reuse seven completed seed evaluations; restart generation under new identity'}
        if args.deepseek_debug:
            previous = ledger_status(prior/'provider')
            rejected = [row for row in previous['calls'] if row['state']!='success']
            if (previous['model']!=DEBUG_MODEL or previous['endpoint']!=CHAT_URL
                    or previous['usage_unknown_calls']!=1
                    or len(rejected)!=1 or rejected[0]['state']!='http_error'
                    or rejected[0]['error'] not in {'HTTP 500','HTTP 503'}
                    or not rejected[0]['call_id'].startswith('debug-0001-')
                    or list(prior.glob('generation-*.json'))):
                raise ValueError('Seed reuse requires a settled early-generation upstream 500/503')
            plan['seed_reuse'].update(prior_requests=previous['requests'], rejected_call=rejected[0])
        else:
            plan['max_requests'] -= 7 * (2 * CALLS_PER_EPISODE + 1)
    if args.plan_only:
        print(json.dumps(plan, indent=2))
        return
    dest = root / 'runs' / args.job_id
    if args.deepseek_debug:
        import os
        os.umask(0o077)
    dest.mkdir(parents=True)
    write_json(dest / 'manifest.json', plan)
    write_json(dest / 'status.json', {'status': 'running'})
    try:
        write_json(dest / 'result.json', execute(root, args, data, source, dest, prior))
        write_json(dest / 'status.json', {'status': 'completed'})
    except Exception as exc:
        write_json(dest / 'status.json', {'status': 'failed', 'error_type': type(exc).__name__, 'error': str(exc)})
        raise


if __name__ == '__main__':
    main()
