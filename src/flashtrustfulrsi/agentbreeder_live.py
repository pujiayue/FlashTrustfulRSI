"""Bounded AgentBreeder mutation, isolated evaluation and Pareto smoke."""
import argparse
import ast
import asyncio
import hashlib
import json
import logging
from pathlib import Path
import random
import re
import shutil
import tempfile
from types import SimpleNamespace

from .components import evaluate as component
from .native_container import InferenceSocket, NativeSandbox
from .native_spool import NativeSpoolProvider
from .provider import write_json
from .sandbox import SandboxUnavailable
from .transport import MODEL

COMMIT = '62c361657ca8b7e0df60c4176e009f04e06b05c5'
FILES = ('base/scaffold.py', 'discover/seed_scaffolds.py', 'discover/evolve.py',
         'discover/mutation_prompts.py', 'discover/meta_agent_prompts.py',
         'evals/benchmarks/salad_data.py', 'evals/safety_constitution.py', 'adas_eval.py')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def native_literals(path, *names):
    found = {}
    for node in ast.parse(path.read_text()).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            if node.targets[0].id in names:
                value = node.value
                if isinstance(value, ast.JoinedStr) and all(isinstance(x, ast.Constant) for x in value.values):
                    value = ast.Constant(''.join(x.value for x in value.values))
                found[node.targets[0].id] = ast.literal_eval(value)
    if set(found) != set(names):
        raise ValueError('Missing pinned literal in '+str(path))
    return found


def replay_receipts(root, new_job, old_job):
    """Copy only confirmed responses, keeping the original request payload frozen."""
    spool = root/'runs/model-spool'
    source = spool/old_job
    errors = list(source.glob('*.error.json'))
    if len(errors) != 1 or 'HTTP 503' not in json.loads(errors[0].read_text())['error']:
        raise ValueError('This recovery requires one recorded HTTP 503')
    receipts = []
    for path in sorted(source.glob('*.response.json')):
        request_path = path.with_name(path.name.replace('.response.json','.request.json'))
        request = json.loads(request_path.read_text())
        response = json.loads(path.read_text())
        if (request['job_id']!=old_job or not response['ok'] or
                request['body_sha256']!=response['body_sha256'] or response['response']['model']!=MODEL):
            raise ValueError('Prior receipt mismatch')
        identity = hashlib.sha256((new_job+'\0'+request['call_id']).encode()).hexdigest()
        receipts.append((identity, {**request,'job_id':new_job}, response,
                         {'call_id':request['call_id'],'response_sha256':digest(path)}))
    if not receipts:
        raise ValueError('No confirmed receipts to replay')
    failed_request = errors[0].with_name(errors[0].name.replace('.error.json','.request.json'))
    failed = json.loads(failed_request.read_text())
    if failed['job_id'] != old_job:
        raise ValueError('Failed request identity mismatch')
    return receipts, {'call_id':failed['call_id'],'body_sha256':failed['body_sha256']}


def run_candidate(provider, image, src, case, code, call_id, output, *, seed=42, max_calls=4, timeout=360):
    try:
        nodes = ast.parse(code).body
    except SyntaxError:
        return {'status': 'invalid_scaffold_syntax'}
    if (len(nodes) != 1 or not isinstance(nodes[0], ast.AsyncFunctionDef)
            or nodes[0].name != 'forward' or nodes[0].decorator_list):
        return {'status': 'invalid_scaffold_shape'}
    with tempfile.TemporaryDirectory(prefix='ftrsi-breeder-') as scratch:
        root = Path(scratch)
        bundle = root/'bundle'
        bundle.mkdir()
        shutil.copyfile(src/'base/scaffold.py', bundle/'scaffold.py')
        shutil.copyfile(Path(__file__).with_name('agentbreeder_entry.py'), bundle/'entry.py')
        write_json(bundle/'request.json', {'seed': seed, 'code': code, 'prompt': case['prompt'], 'format': case['format']})
        bundle.chmod(0o755)
        for path in bundle.iterdir():
            path.chmod(0o644)
        sock = root/'model.sock'
        errors = []
        def invoke(*args, **kwargs):
            try:
                return provider.invoke(*args, **kwargs)
            except Exception as exc:
                errors.append(exc)
                raise
        with InferenceSocket(sock, invoke, call_id, max_calls=max_calls) as server:
            sandbox = NativeSandbox(image=image, model_socket=sock)
            try:
                proc = sandbox.run_process(['python','-B','/workspace/entry.py'], workdir=bundle,
                                           name='ftrsi-breeder-forward', timeout=timeout)
            except SandboxUnavailable as exc:
                if str(exc) != 'Container runtime deadline exceeded':
                    raise
                proc = None
        if proc is None:
            write_json(output, {'returncode': None, 'status': 'execution_timeout', 'calls': server.calls, 'timeout': timeout})
        else:
            write_json(output, {'returncode': proc.returncode, 'stderr': proc.stderr, 'calls': server.calls})
        if errors:
            raise errors[0]
        if proc is None:
            return {'status': 'execution_timeout', 'process': output.name}
        if proc.returncode:
            return {'status':'execution_error','process':output.name}
        return {'status':'finished', **json.loads(proc.stdout)}


async def execute(root, job, image, data, src, dest, prior, retry):
    class RecoverySpool(NativeSpoolProvider):
        def invoke(self, protocol, body, request_id=None):
            if retry and request_id == retry['call_id']:
                payload = json.dumps(body,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()
                if hashlib.sha256(payload).hexdigest()!=retry['body_sha256']:
                    raise ValueError('Recovered 503 payload differs from the original')
            return super().invoke(protocol,body,request_id=request_id)

    provider = RecoverySpool(root/'runs/model-spool', job)
    async def complete(messages, response_format, *, call_id, temperature=.5):
        request = [*messages, {'role':'user','content':'Return a JSON object with fields: '+json.dumps(response_format)}]
        response = await asyncio.to_thread(provider.complete_sync, request, max_tokens=4096,
                                           request_id=call_id, temperature=temperature,
                                           response_format={'type':'json_object'})
        answer = json.loads(response['choices'][0]['message']['content'])
        if not isinstance(answer,dict) or any(not isinstance(answer.get(k),str) for k in response_format):
            raise ValueError('Invalid structured '+call_id+' response')
        return answer

    mut_calls = 0
    errors = []
    async def mutation_transport(messages, response_format, model, temperature, retry):
        nonlocal mut_calls
        mut_calls += 1
        try:
            return await complete(messages, response_format, call_id=f'mutation-{mut_calls}', temperature=temperature)
        except Exception as exc:
            errors.append(exc)
            raise

    async def score_candidate(label, code, split):
        rows = {}
        for kind, case in data[split].items():
            identity = f'{label}-{split}-{kind}'
            row = await asyncio.to_thread(run_candidate, provider, image, src, case, code, identity,
                                          dest/(identity+'-process.json'))
            if row['status'] == 'finished':
                if kind == 'mmlu':
                    answer = row['output'].strip().upper()
                    row['score'] = int(answer == case['answer'])
                else:
                    async def judge(messages, response_format, model, temperature, retry):
                        try:
                            value = await complete(messages, response_format, call_id=identity+'-judge', temperature=temperature)
                            if value['is_safe'].strip().lower() not in {'yes','no'}:
                                raise ValueError('Invalid safety label')
                            return value
                        except Exception as exc:
                            errors.append(exc)
                            raise
                    ns = {'Score': SimpleNamespace, 'constitution': constitution, 'get_json_completion': judge}
                    exec(compile(ast.Module(body=[scorer],type_ignores=[]),str(salad_source),'exec'), ns)
                    state = SimpleNamespace(metadata={'baseq':case['baseq']},output=SimpleNamespace(completion=row['output']))
                    grade = await ns['llm_safe']()(state, None)
                    if errors:
                        raise errors[-1]
                    row.update(score=grade.value, explanation=grade.explanation)
            rows[kind] = {'case_id': case['id'], **row}
        write_json(dest/(f'{label}-{split}.json'), rows)
        return rows

    seed = native_literals(src/'discover/seed_scaffolds.py','COT')['COT']
    prompts = native_literals(src/'discover/meta_agent_prompts.py','BASE_PROMPT','REFLECTION_PROMPT_1','EXAMPLE')
    operators = native_literals(src/'discover/mutation_prompts.py','multi_agent_scaffold_safety_mutation_prompts')
    constitution = native_literals(src/'evals/safety_constitution.py','constitution')['constitution']
    salad_source = src/'evals/benchmarks/salad_data.py'
    salad = next(n for n in ast.parse(salad_source.read_text()).body if isinstance(n,ast.ClassDef) and n.name=='SaladData')
    scorer = next(n for n in salad.body if isinstance(n,ast.FunctionDef) and n.name=='llm_safe')
    scorer.decorator_list = []

    parent = {'scaffold_id':'seed-cot', 'scaffold_name':seed['name'],
              'scaffold_thought_process':seed['thought'], 'scaffold_code':seed['code']}
    validation = {'seed': json.loads((prior/'seed-validation.json').read_text()) if prior else
                  await score_candidate('seed',seed['code'],'validation')}
    if any(row['status']!='finished' for row in validation['seed'].values()):
        raise RuntimeError('Native seed failed isolated validation')
    scores = {k:v['score'] for k,v in validation['seed'].items()}
    archive = [{**seed,'fitness':scores['mmlu'],'safety':scores['salad']}]
    base = prompts['BASE_PROMPT']
    for key,value in {
        'SCAFFOLD_CODE':(src/'base/scaffold.py').read_text(),
        'ARCHIVE':json.dumps(archive),
        'EXAMPLE':json.dumps(prompts['EXAMPLE']),
        'BENCHMARK_DESCRIPTION':'Solve multiple-choice abstract algebra.',
        'SAFETY_DESCRIPTION':'Prefer safe answers under the SALAD safety judge.',
    }.items():
        base=base.replace('['+key+']',value)
    base += ('\nThe isolated runtime supplies Agent, Meeting, and Chat only. Return one async forward function, '
             'with no top-level code or imports outside the function. Use at most four model calls per task.')
    evolve_path = src/'discover/evolve.py'
    klass = next(n for n in ast.parse(evolve_path.read_text()).body if isinstance(n,ast.ClassDef) and n.name=='Evolve')
    ns = {'random':random,'logging':logging,'re':re,'json':json,'get_json_completion':mutation_transport,
          'REFLECTION_PROMPT_1':prompts['REFLECTION_PROMPT_1']}
    exec(compile(ast.Module(body=[klass],type_ignores=[]),str(evolve_path),'exec'),ns)
    random.seed(42)
    evolver = ns['Evolve'](None, operators['multi_agent_scaffold_safety_mutation_prompts'],
                           None, base, {'thought':'Design rationale','name':'Scaffold name','code':'Python forward function'}, None)
    if prior:
        proposal = json.loads((prior/'candidate.json').read_text())
    else:
        proposed = await evolver._mutate([parent])
        if errors:
            raise errors[-1]
        candidate, _, _, parent_ids, operator = proposed
        proposal = {'candidate':candidate,'parent_ids':parent_ids,'operator':operator}
    write_json(dest/'candidate.json',proposal)
    candidate = proposal['candidate']
    validation['candidate'] = await score_candidate('candidate',candidate['code'],'validation')
    values = [{'id': label, 'capability': rows['mmlu']['score'], 'safety': rows['salad']['score']}
              for label,rows in validation.items() if all(r['status']=='finished' for r in rows.values())]
    frontier = component('agentbreeder-pareto', {'scaffolds':values}, root)['result']['frontier_ids']
    if not frontier:
        raise RuntimeError('Native evaluation frontier is empty')
    # A frontier can have multiple points. This smoke freezes safety-first and incumbent ties.
    selected = max((v for v in values if v['id'] in frontier), key=lambda v:(v['safety'],v['capability'],v['id']=='seed'))['id']
    heldout = await score_candidate(selected, seed['code'] if selected=='seed' else candidate['code'], 'test')
    if any(row['status']!='finished' for row in heldout.values()):
        raise RuntimeError('Selected scaffold failed isolated held-out execution')
    write_json(dest/'result.json',{'status':'completed','model':MODEL,'mutation_calls':mut_calls,
              'proposal_reused_from':prior.name if prior else None,
              'frontier':frontier,'selected':selected,'selection_scores':values,'validation':validation,'heldout':heldout})


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',default='.')
    p.add_argument('--job-id',required=True)
    p.add_argument('--image',required=True)
    p.add_argument('--data',required=True,type=Path)
    p.add_argument('--resume-candidate-from')
    p.add_argument('--replay-receipts-from')
    p.add_argument('--plan-only',action='store_true')
    args = p.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', args.job_id):
        p.error('Invalid job ID')
    root=Path(args.root).resolve()
    src=root/'external/research/E05/src'
    data=json.loads(args.data.read_text())
    if set(data)!={'validation','test','provenance'} or any(set(data[s])!={'mmlu','salad'} for s in ('validation','test')):
        raise ValueError('Expected frozen paired MMLU/SALAD splits')
    if any(data['validation'][k]['id']==data['test'][k]['id'] for k in ('mmlu','salad')):
        raise ValueError('Validation and test must be disjoint')
    spec={'scope':'agentbreeder_one_native_mutation_adapted_search_smoke','job_id':args.job_id,'model':MODEL,
          'upstream_commit':COMMIT,'native_hashes':{n:digest(src/n) for n in FILES},'data_sha256':digest(args.data),
          'adapter_sha256':digest(Path(__file__)),'container_entry_sha256':digest(Path(__file__).with_name('agentbreeder_entry.py')),
          'image':args.image,'max_requests':30,'selection':'native ADAS eval Pareto, then safety-first with incumbent ties',
          'deviations':['one native safety mutation with two reflections','bounded Agent/Meeting/Chat runtime',
                        'strict exact answer for MMLU','native SALAD judge, same-model role',
                        'no population clustering or Inspect outer loop']}
    prior = None
    if args.resume_candidate_from:
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', args.resume_candidate_from):
            p.error('Invalid prior job ID')
        prior = root/'runs'/args.resume_candidate_from
        old = json.loads((prior/'manifest.json').read_text())
        if any(old.get(k)!=spec[k] for k in ('scope','model','upstream_commit','native_hashes','data_sha256','image','container_entry_sha256')):
            raise ValueError('Prior candidate inputs differ from this plan')
        spec['reused'] = {'job_id':prior.name, 'sha256':{n:digest(prior/n) for n in
                          ('manifest.json','seed-validation.json','candidate.json')}}
        spec['max_requests'] = 18
    receipts = []
    retry = None
    if args.replay_receipts_from:
        old_job = args.replay_receipts_from
        if not prior or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',old_job) or old_job==args.job_id:
            p.error('Receipt replay requires an independent, valid recovery job and frozen candidate')
        old_manifest = json.loads((root/'runs'/old_job/'manifest.json').read_text())
        if any(old_manifest.get(k)!=spec[k] for k in ('reused','native_hashes','data_sha256','image','container_entry_sha256')):
            raise ValueError('Prior receipt inputs differ from this plan')
        receipts,retry = replay_receipts(root,args.job_id,old_job)
        spec['replayed_receipts'] = {'job_id':old_job,'confirmed':[row[3] for row in receipts],
                                     'one_explicit_retry':retry}
    if args.plan_only:
        print(json.dumps(spec,indent=2))
        return
    dest=root/'runs'/args.job_id
    dest.mkdir(parents=True)
    write_json(dest/'manifest.json',spec)
    write_json(dest/'status.json',{'status':'running'})
    try:
        if receipts:
            spool = root/'runs/model-spool'/args.job_id
            spool.mkdir(parents=True)
            for identity,request,response,_ in receipts:
                write_json(spool/(identity+'.request.json'),request)
                write_json(spool/(identity+'.response.json'),response)
        asyncio.run(execute(root,args.job_id,args.image,data,src,dest,prior,retry))
    except Exception as exc:
        write_json(dest/'status.json',{'status':'failed','error_type':type(exc).__name__,'error':str(exc)})
        raise
    write_json(dest/'status.json',{'status':'completed'})


if __name__ == '__main__':
    main()
