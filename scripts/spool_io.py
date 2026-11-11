"""Read/write experiment request payloads; this host has no API credential."""
import argparse,hashlib,json,re,sys
from pathlib import Path
from flashtrustfulrsi.provider import write_json

def main():
    p=argparse.ArgumentParser();p.add_argument('action',choices=('next','put'));p.add_argument('--job-id',required=True)
    p.add_argument('--phase-id');args=p.parse_args()
    if not re.fullmatch('[A-Za-z0-9_-]{1,80}',args.job_id):raise ValueError('Invalid job')
    if args.phase_id and not re.fullmatch('[A-Za-z0-9_-]{1,80}',args.phase_id):raise ValueError('Invalid phase')
    base=Path('runs/model-spool')/args.job_id;base.mkdir(parents=True,exist_ok=True)
    if args.action=='next':
        status=(Path('runs')/args.phase_id if args.phase_id else Path('runs'))/args.job_id/'status.json';state=json.loads(status.read_text()) if status.exists() else {'status':'not_started'}
        for path in sorted(base.glob('*.request.json')):
            name=path.name.removesuffix('.request.json')
            if not (base/(name+'.response.json')).exists() and not (base/(name+'.error.json')).exists():
                print(json.dumps({'status':state,'request':json.loads(path.read_text())},ensure_ascii=False));return
        print(json.dumps({'status':state,'request':None}));return
    item=json.load(sys.stdin);request=item['request'];response=item['response']
    if request['job_id']!=args.job_id:raise ValueError('Job mismatch')
    name=hashlib.sha256((args.job_id+'\0'+request['call_id']).encode()).hexdigest()
    if json.loads((base/(name+'.request.json')).read_text())!=request:raise ValueError('Original request changed')
    suffix='.response.json' if response['ok'] else '.error.json'
    path=base/(name+suffix)
    if path.exists() and json.loads(path.read_text())!=response:raise ValueError('Frozen response changed')
    write_json(path,response);print(json.dumps({'stored':True,'call_id':request['call_id'],'ok':response['ok']}))

if __name__=='__main__':main()
