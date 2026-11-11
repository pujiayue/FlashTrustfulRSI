"""Passive, hash-bound evidence envelope for specialized native RSI protocols.

This layer never executes an updater, loads generated code, invents a metric,
or changes native admission. Raw result schemas remain native and are linked.
"""
import argparse
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3
from .provider import write_json

SCHEMA='flashtrustfulrsi.evidence.v1'
DIMENSIONS={'D1','D2','D3','D4','D5','D6'}
SURFACES={'Context','Memory','Skill','Code','Tool','Workflow','Model'}
STAGES={'S1','S2','S3','S4'}
OFFICIAL={'https://api.deepseek.com/chat/completions','https://api.deepseek.com/anthropic/v1/messages'}


def collect(run, *, method, dimensions, surfaces, stages):
    run=Path(run).resolve()
    if not re.fullmatch(r'dev-dsflash-[a-z0-9-]+',run.name):raise ValueError('A dedicated developer run is required')
    for values,allowed in ((dimensions,DIMENSIONS),(surfaces,SURFACES),(stages,STAGES)):
        if not values or set(values)-allowed:raise ValueError('Unknown or empty taxonomy values')
    manifest=json.loads((run/'manifest.json').read_text());status=json.loads((run/'status.json').read_text())
    model=manifest.get('model',manifest.get('settings',{}).get('model'))
    if manifest.get('runs_formal_eval') is not False or model!='deepseek-flash':
        raise ValueError('Official developer manifest required')
    artifacts=[]
    for path in sorted(run.rglob('*')):
        if path.is_symlink():raise ValueError('Evidence symlinks are not portable')
        if path.is_file() and path.suffix=='.json' and path.name!='evidence-bundle.json':
            artifacts.append({'path':str(path.relative_to(run)),'sha256':sha256(path.read_bytes()).hexdigest(),'bytes':path.stat().st_size})
    ledgers=[];states=Counter()
    for path in sorted(run.rglob('deepseek-debug.sqlite3')):
        with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as db:
            settings=json.loads(db.execute('SELECT value FROM settings WHERE id=1').fetchone()[0])
            if settings['model']!='deepseek-flash' or settings['endpoint'] not in OFFICIAL:raise ValueError('Unexpected model route')
            rows=db.execute('SELECT call_id,status,request,response,error FROM calls ORDER BY started,call_id').fetchall()
        summary=[]
        for call_id,state,request,response,error in rows:
            body=json.loads(request)
            if body['model']!='deepseek-flash':raise ValueError('Unexpected request model')
            if state=='success' and json.loads(response)['model']!='deepseek-flash':raise ValueError('Unexpected response model')
            states[state]+=1
            summary.append({'call_id':call_id,'state':state,'request_sha256':sha256(request.encode()).hexdigest(),
                            'response_sha256':sha256(response.encode()).hexdigest() if response else None,'error':error})
        ledgers.append({'path':str(path.relative_to(run)),'endpoint':settings['endpoint'],'calls':summary})
    return {'schema':SCHEMA,'method':method,'job_id':run.name,'scope':'development_only',
            'runs_formal_eval':False,'model':'deepseek-flash',
            'taxonomy':{'dimensions':list(dimensions),'surfaces':list(surfaces),'stages':list(stages)},
            'native_status':status,'receipt_states':dict(states),'unsettled_requests':states['pending'],
            'ledgers':ledgers,'artifacts':artifacts,
            'native_result':'result.json' if (run/'result.json').exists() else None,
            'metric_policy':'Native results retain their own schemas. Missing/error safety remains null; this envelope does not create scores.',
            'release_policy':'Development evidence only. Successful transport is separate from native execution, selection, and scientific acceptance.'}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--run',required=True);p.add_argument('--method',required=True)
    p.add_argument('--dimensions',required=True);p.add_argument('--surfaces',required=True);p.add_argument('--stages',required=True)
    args=p.parse_args();out=collect(args.run,method=args.method,dimensions=args.dimensions.split(','),
                                 surfaces=args.surfaces.split(','),stages=args.stages.split(','))
    write_json(Path(args.run)/'evidence-bundle.json',out)
    print(json.dumps({key:out[key] for key in ('schema','job_id','native_status','receipt_states','unsettled_requests')},indent=2))
if __name__=='__main__':main()
