"""Full published EvoPath stream pool, three campaigns, native decision frequency.

One independent job is a complete stream/campaign with all three paired states.
The queue stops admitting jobs after any failure; ambiguous calls never retry.
"""
import argparse, concurrent.futures, fcntl, hashlib, json, subprocess, sys, time
from collections import Counter
from pathlib import Path
from .provider import write_json
from .transport import MODEL, RecordedProvider, TransportError
from .reviewed_recovery import ReviewedProvider

COMMIT = 'a0a596237d888304c25be2828d30c99e2f66b3b7'
CONDITIONS = ['baseline', 'episodic_memory']


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source(root):
    paths = list((root/'upstream').glob('HQ-Lin-EvoPathBench-*'))
    if len(paths) != 1:
        raise ValueError('Exactly one pinned EvoPath checkout required')
    return paths[0]


def settings(endpoint='https://configured-gateway.example/v1'):
    from evolens.agent import ModelConfig
    return ModelConfig(model=MODEL, base_url=endpoint, api_key_env='FTRSI_SECRET_REF_ONLY',
        temperature=.1, max_retries=0, decision_interval=8, save_raw_responses=True,
        max_consecutive_failures=1)


def build_plan(root, phase, recovery_review=None):
    from evolens.dataset import load_dataset
    from evolens.agent import estimate_model_calls
    up = source(root)
    scenarios, streams, dataset = load_dataset(up/'data/finance')
    for name, expected in dataset['sha256'].items():
        if digest(up/'data/finance'/name) != expected:
            raise ValueError('Published dataset hash mismatch: '+name)
    if len(streams) != 1800 or len({s.stream_id for s in streams}) != 1800:
        raise ValueError('Expected all 1800 unique published streams')
    types = Counter(s.template for s in streams)
    if types != {'accumulation': 600, 'interference': 600, 'reversal': 600}:
        raise ValueError('Unexpected stream strata')
    config = settings()
    groups = {kind: [s for s in streams if s.template == kind] for kind in sorted(types)}
    # Interleave strata before later shards; all jobs remain independent.
    ordered = [groups[kind][i] for i in range(600) for kind in sorted(groups)]
    jobs = []
    for stream in ordered:
        estimate = estimate_model_calls(scenarios, [stream], CONDITIONS, config, 1, 1, ['episodic_memory'])
        for campaign in (42, 43, 44):
            identity = hashlib.sha256(f'{stream.stream_id}|{campaign}'.encode()).hexdigest()[:20]
            jobs.append({'job_id': 't2-'+identity, 'stream_id': stream.stream_id,
                'template': stream.template, 'campaign_id': campaign, 'estimate': estimate,
                'limits': {'requests': estimate['logical_calls'],
                    'input_tokens': estimate['logical_calls']*131072,
                    'output_tokens': estimate['output_token_upper_bound']}})
    if recovery_review and recovery_review['job_id'] not in {j['job_id'] for j in jobs}:raise ValueError('Recovery job outside plan')
    return {'schema': 'flashtrustfulrsi.formal.t2.v2', 'phase_id': phase, 'model': MODEL,
        'upstream_commit': COMMIT, 'dataset_manifest': dataset,
        'dataset_files': {str(p.relative_to(up/'data/finance')): digest(p)
            for p in sorted((up/'data/finance').rglob('*')) if p.is_file()},
        'source_files': {str(p.relative_to(up)): digest(p)
            for p in sorted((up/'evolens').rglob('*.py'))},
        'adapter_sha256': digest(Path(__file__)),
        'transport_sha256': digest(Path(__file__).with_name('transport.py')),
        'protocol_files':{name:digest(Path(__file__).with_name(name)) for name in ('native_transport.py','reviewed_recovery.py')},
        'http_timeout_seconds':600,'recovery_review':recovery_review,
        'settings': config.public_dict(), 'campaigns': [42,43,44], 'repeats': 1,
        'conditions': CONDITIONS, 'state_off_conditions': ['episodic_memory'],
        'native_cli_decision_interval': 8, 'stream_count': 1800, 'job_count': len(jobs),
        'total_estimate': estimate_model_calls(scenarios, streams, CONDITIONS, config, 3, 1, ['episodic_memory']),
        'analysis': {'unit': 'independent campaign and stream; never individual probe',
            'native_metrics': ['paired_ceg','state_utilization_effect','forgetting','mechanism','risk'],
            'bootstrap_seed': 20261105, 'bootstrap_repetitions': 10000,
            'interval': 'paired hierarchical campaign/stream bootstrap; report three campaign values',
            'limitation': 'only three independent campaign seeds; intervals must not imply many independent campaigns'},
        'deviations': ['gpt-5.6-sol for all LLM roles', 'recorded transport without automatic retries',
            'all 1800 published calibrated semi-synthetic streams; not the live/replay market track'],
        'jobs': jobs}


def run_job(root, plan_path, job_id):
    from evolens.agent import ModelTradingAgent, ModelRequestError
    from evolens.dataset import load_dataset
    from evolens.runner import BenchmarkRunner, write_run
    from evolens.cli import _dataset_market
    from .evaluators import evopath_summary
    plan = json.loads(plan_path.read_text())
    if digest(Path(__file__)) != plan['adapter_sha256'] or digest(Path(__file__).with_name('transport.py')) != plan['transport_sha256']:
        raise ValueError('Frozen implementation changed')
    for name,expected in plan['protocol_files'].items():
        if digest(Path(__file__).with_name(name))!=expected:raise ValueError('Frozen protocol changed')
    item = next(j for j in plan['jobs'] if j['job_id'] == job_id)
    up = source(root)
    for rel, expected in plan['source_files'].items():
        if digest(up/rel) != expected: raise ValueError('Frozen upstream source changed: '+rel)
    for rel, expected in plan['dataset_files'].items():
        if digest(up/'data/finance'/rel) != expected: raise ValueError('Frozen dataset changed: '+rel)
    dest = root/'runs'/plan['phase_id']/job_id
    dest.mkdir(parents=True, exist_ok=True)
    with (dest/'job.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        manifest = {'plan_sha256': digest(plan_path), **item}
        if (dest/'manifest.json').exists() and json.loads((dest/'manifest.json').read_text()) != manifest:
            raise ValueError('Job manifest changed')
        write_json(dest/'manifest.json', manifest)
        if (dest/'result.json').exists(): return
        config = json.loads((root/'configs/provider-v02.json').read_text())
        review=plan.get('recovery_review')
        if review and review['job_id']!=job_id:review=None
        provider = ReviewedProvider(config, dest/'provider', job_id, limits=item['limits'],review=review,project_root=root)
        native_config = settings(config['base_url'])
        def complete(messages, model_config, max_tokens, phase=None):
            start = time.monotonic()
            try:
                response = provider.invoke('chat', {'model':MODEL, 'messages':messages,
                    'max_tokens':max_tokens, 'temperature':model_config.temperature})
            except TransportError as exc:
                raise ModelRequestError(str(exc)) from exc
            print(json.dumps({'call':provider.sequence,'phase':phase,'usage':response.get('usage',{})}), flush=True)
            return {'content':response['choices'][0]['message'].get('content',''),
                'usage':response.get('usage',{}),'request_id':response.get('id'),
                'response_model':response['model'],'finish_reason':response['choices'][0]['finish_reason'],
                'elapsed_seconds':time.monotonic()-start,
                'attempts':[{'provider_receipt_call_id':f'{provider.sequence:08d}'}]}
        scenarios, streams, dataset = load_dataset(up/'data/finance')
        selected = [s for s in streams if s.stream_id == item['stream_id']]
        if len(selected) != 1: raise ValueError('Missing frozen stream')
        write_json(dest/'status.json', {'status':'running','started':time.time()})
        try:
            factory = lambda condition, seed: ModelTradingAgent(condition,seed,native_config,completion_fn=complete)
            result = BenchmarkRunner(scenarios, selected, agent_factory=factory, dataset_manifest=dataset,
                market_simulator=_dataset_market(up/'data/finance',dataset)).run(CONDITIONS,
                campaigns=1,campaign_offset=item['campaign_id'],repeats=1,
                state_off_conditions=['episodic_memory'],capture_agent_traces=True,max_concurrency=1)
            write_run(dest, result, audit_layout=True)
            summary = evopath_summary([r.to_dict() for r in result['evaluation_records']], result['training_records'], result['state_records'])
            if summary != result['summary']: raise ValueError('Independent native scorer mismatch')
            usage = provider.summary()
            if usage['logical_states'] != {'success':item['estimate']['logical_calls']}:
                raise ValueError('Actual request denominator differs from frozen estimate')
            write_json(dest/'result.json', {'status':'completed','scope':'formal_T2_native_stream_campaign',
                'job_id':job_id,'stream_id':item['stream_id'],'campaign_id':item['campaign_id'],
                'evaluation_records':len(result['evaluation_records']), 'training_records':len(result['training_records']),
                'state_records':len(result['state_records']),'usage':usage,'scorer_exact_match':True,'summary':summary})
            write_json(dest/'status.json', {'status':'completed','finished':time.time()})
        except Exception as exc:
            write_json(dest/'status.json', {'status':'failed','error_type':type(exc).__name__,
                'error':str(exc),'usage':provider.summary(),'finished':time.time()})
            raise


def queue(root, plan_path, workers):
    plan = json.loads(plan_path.read_text()); dest = root/'runs'/plan['phase_id']
    dest.mkdir(parents=True,exist_ok=True)
    with (dest/'queue.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        def one(item):
            jobdir = dest/item['job_id'];jobdir.mkdir(exist_ok=True)
            with (jobdir/'process.log').open('a') as log:
                return subprocess.run([sys.executable,'-m','flashtrustfulrsi.evopath_formal','job',
                    '--root',str(root),'--plan',str(plan_path),'--job-id',item['job_id']],stdout=log,stderr=log).returncode
        pending_jobs = [j for j in plan['jobs'] if not (dest/j['job_id']/'result.json').exists()]
        active = {}; failed=[]; completed=plan['job_count']-len(pending_jobs); index=0
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            while active or (index<len(pending_jobs) and not failed):
                while not failed and len(active)<workers and index<len(pending_jobs):
                    item=pending_jobs[index];index+=1;active[pool.submit(one,item)]=item['job_id']
                write_json(dest/'queue-status.json', {'status':'draining_after_failure' if failed else 'running',
                    'completed':completed,'total':plan['job_count'],'active':list(active.values()),'failed':failed,'updated':time.time()})
                done,_=concurrent.futures.wait(active,return_when=concurrent.futures.FIRST_COMPLETED)
                for future in done:
                    job=active.pop(future)
                    if future.result(): failed.append(job)
                    else: completed+=1
            write_json(dest/'queue-status.json',{'status':'stopped_on_failure' if failed else 'completed',
                'completed':completed,'total':plan['job_count'],'active':[],'failed':failed,'updated':time.time()})
        if failed: raise RuntimeError('Queue stopped; reconcile failed jobs before resuming')


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['plan','job','queue'])
    p.add_argument('--root',default='.');p.add_argument('--plan',required=True)
    p.add_argument('--phase',default='formal-t2-v04b');p.add_argument('--job-id');p.add_argument('--recovery-review',type=Path)
    p.add_argument('--workers',type=int,default=4);a=p.parse_args();root=Path(a.root).resolve();path=Path(a.plan).resolve()
    if not 1<=a.workers<=8:raise ValueError('Workers must be 1..8')
    if not a.phase.replace('-','').replace('_','').isalnum():raise ValueError('Invalid phase')
    if a.action=='plan':
        plan=build_plan(root,a.phase,json.loads(a.recovery_review.read_text()) if a.recovery_review else None)
        if path.exists() and json.loads(path.read_text())!=plan:raise ValueError('Frozen plan exists with different content')
        write_json(path,plan);print(json.dumps({'jobs':plan['job_count'],'estimate':plan['total_estimate']}))
    elif a.action=='job':run_job(root,path,a.job_id)
    else:queue(root,path,a.workers)


if __name__=='__main__':main()
