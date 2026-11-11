"""Resume only sealed frozen audits; never rerun native search or unresolved calls."""
import copy
import json
from pathlib import Path

from .composition import digest
from .deepseek_debug import ledger_status


def restore_rows(values,tasks):
    """Rebind JSON records only after exact public Task serialization matches.

    Native immutable tuples serialize as JSON arrays; rebuilding Task(**json)
    alone does not restore those tuple types. No task text/label is repaired.
    """
    from flashrsi.core import Trajectory
    from flashrsi._records import _trajectory_record
    expected=[json.loads(json.dumps(_trajectory_record(Trajectory(task=task))['task'],default=dict)) for task in tasks]
    if [row['task'] for row in values]!=expected:raise ValueError('Cached audit membership changed')
    return tuple(Trajectory(**{**row,'task':task}) for row,task in zip(values,tasks,strict=True))


def prepare(source):
    """Read-only recovery plan. Completed valid role files can be reused exactly."""
    from flashrsi import runner
    from flashrsi.core import Snapshot,Task,Trajectory
    source=Path(source).resolve()
    manifest=json.loads((source/'manifest.json').read_text())
    status=json.loads((source/'status.json').read_text())
    if manifest.get('model')!='deepseek-flash' or manifest.get('runs_formal_eval') is not False:
        raise ValueError('Official development run required')
    if status['status'] not in ('failed','completed'):
        raise ValueError('Recovery requires a terminal source run; do not race a running job')
    receipts=[]
    for db in sorted(source.rglob('deepseek-debug.sqlite3')):
        ledger=ledger_status(db.parent)
        if ledger['model']!='deepseek-flash' or ledger['endpoint'] not in (
                'https://api.deepseek.com/chat/completions','https://api.deepseek.com/anthropic/v1/messages'):
            raise ValueError('Unexpected source provider')
        if any(row['state'] in ('pending','transport_unknown') for row in ledger['calls']):
            raise ValueError('Reconcile unresolved source receipts before opening a new audit attempt')
        receipts.append({'path':str(db.parent.relative_to(source)), 'requests':ledger['requests'],
                         'states':{state:sum(row['state']==state for row in ledger['calls']) for state in {r['state'] for r in ledger['calls']}}})
    trust=source/'trust';seal=json.loads((trust/'selection.json').read_text());snapshots={}
    for role in ('initial','selected'):
        path=trust/(role+'.json');snapshot=Snapshot.load(path);expected=seal['snapshots'][role]
        if digest(path)!=expected['sha256'] or snapshot.id!=expected['id']:
            raise ValueError('Sealed '+role+' snapshot changed')
        snapshots[role]={'path':str(path),'sha256':digest(path)}
    # The seed is required to reopen the exact native Snapshot root, not a new seed.
    seed=Path(manifest.get('frozen',{}).get('seed_dir',source/'seed')).resolve()
    agent=runner._AGENTS[manifest['harness']]({'name':manifest['harness'],'source_dir':str(seed),
          'llm':{'model_name':'deepseek-flash','api_key':'no-secret','base_url':'http://model-gateway/v1'}})
    if agent.snapshot.id!=seal['snapshots']['initial']['id']:
        raise ValueError('The original native seed root changed')
    spec=copy.deepcopy(manifest);spec['frozen']={'seed_dir':str(seed),**snapshots}
    cache=[];remaining=[]
    for audit in manifest['audits']:
        bench=runner._BENCHMARKS[audit['benchmark']['name']](audit['benchmark']);tasks=bench.load()['test']
        for role in ('initial','selected'):
            path=trust/f'{audit["name"]}-{role}.json'
            if path.exists():
                values=json.loads(path.read_text());rows=restore_rows(values,tasks)
                if all(row.status=='finished' for row in rows):
                    graded=bench.grade(rows)
                    if [dict(row.metrics) for row in graded]!=[row['metrics'] for row in values]:
                        raise ValueError('Cached native metrics do not match independent regrading')
                    cache.append({'audit':audit['name'],'role':role,'path':str(path),'sha256':digest(path),
                                  'snapshot_id':seal['snapshots'][role]['id']});continue
            remaining.append({'audit':audit['name'],'role':role,'tasks':len(tasks)})
    spec['cached_audits']=cache
    spec['recovery']={'source_run':str(source),'manifest_sha256':digest(source/'manifest.json'),
                      'selection_sha256':digest(trust/'selection.json'),'source_receipts':receipts,
                      'policy':'Reuse finished validated roles; only missing/incomplete roles are new inference. Native optimizer is never reconstructed.',
                      'remaining_roles':remaining,'model_requests_during_prepare':0}
    return spec


def read_cache(spec, audits, benchmark_factory):
    """Recheck every cache binding before any native inference."""
    from flashrsi.core import Snapshot,Task,Trajectory
    snapshots={role:Snapshot.load(spec['frozen'][role]['path']) for role in ('initial','selected')}
    configs={audit.name:audit.benchmark for audit in audits};result={}
    for item in spec.get('cached_audits',[]):
        key=(item['audit'],item['role']);path=Path(item['path'])
        if key in result or key[0] not in configs or key[1] not in snapshots:
            raise ValueError('Invalid or duplicate audit cache binding')
        if digest(path)!=item['sha256'] or snapshots[key[1]].id!=item['snapshot_id']:
            raise ValueError('Audit cache or frozen snapshot binding changed')
        values=json.loads(path.read_text())
        bench=benchmark_factory(configs[key[0]]);tasks=bench.load()['test'];rows=restore_rows(values,tasks)
        if tuple(row.task for row in rows)!=tuple(tasks) or any(row.status!='finished' for row in rows):
            raise ValueError('Only complete matching native observations are reusable')
        if [dict(row.metrics) for row in bench.grade(rows)]!=[row['metrics'] for row in values]:
            raise ValueError('Native cache scorer mismatch')
        result[key]=rows
    return result
