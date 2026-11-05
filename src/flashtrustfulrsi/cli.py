"""Explicit plugin dispatch; registration never implies validated integration."""
import argparse, importlib, json, sys
from pathlib import Path

PROFILES={'evopath-memory':'evopath_live','minja-qa':'minja','agentdojo-direct':'dojo_live','mextra-synthetic':'mextra','misevolution-service':'misevolution','cimemories':'cim_live'}
def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['list','plan','run','status'])
    parser.add_argument('--profile',choices=PROFILES);parser.add_argument('--root',default='.')
    parser.add_argument('--job-id');args,extra=parser.parse_known_args();root=Path(args.root).resolve()
    if args.command=='list':
        print(json.dumps({'profiles':PROFILES,'catalog':json.loads((root/'configs/catalog.json').read_text())},ensure_ascii=False,indent=2));return
    if args.command=='status':
        rows=[]
        for path in sorted((root/'runs').glob('*/manifest.json')):
            status=path.with_name('status.json');result=path.with_name('result.json')
            rows.append({'job_id':path.parent.name,'status':json.loads(status.read_text()) if status.exists() else {'status':'planned'},
                         'result':str(result) if result.exists() else None})
        print(json.dumps(rows,ensure_ascii=False,indent=2));return
    if not args.profile or not args.job_id:parser.error('--profile and --job-id required')
    sys.argv=[sys.argv[0],'--root',str(root),'--job-id',args.job_id,*extra]+(['--plan-only'] if args.command=='plan' else [])
    importlib.import_module('.'+PROFILES[args.profile],__package__).main()
if __name__=='__main__':main()
