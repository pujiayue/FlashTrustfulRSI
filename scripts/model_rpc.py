"""Execute one recorded request on the original credential host; never export keys."""
import argparse,hashlib,json,re,sys
from pathlib import Path
from flashtrustfulrsi.transport import MODEL,RecordedProvider

def main():
    p=argparse.ArgumentParser();p.add_argument('--root',required=True);p.add_argument('--job-id',required=True)
    args=p.parse_args();root=Path(args.root).resolve();request=json.load(sys.stdin)
    digest=hashlib.sha256(json.dumps(request['body'],sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
    if digest!=request['body_sha256']:raise ValueError('Request body hash mismatch')
    if request['job_id']!=args.job_id or not re.fullmatch('[A-Za-z0-9_-]{1,80}',args.job_id):raise ValueError('Job mismatch')
    if request['protocol']!='chat' or request['body'].get('model')!=MODEL or not 1<=request['body'].get('max_tokens',0)<=2048:
        raise ValueError('Request outside frozen model protocol')
    provider=RecordedProvider(json.loads((root/'configs/provider-v02.json').read_text()),root/'runs/v03-yaml',args.job_id,
                              limits={'requests':48,'input_tokens':2_000_000,'output_tokens':200_000})
    try:
        response=provider.invoke(request['protocol'],request['body'],request_id=request['call_id'])
        print(json.dumps({'ok':True,'body_sha256':request['body_sha256'],'response':response},ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({'ok':False,'error':type(exc).__name__+': '+str(exc)}));sys.exit(1)

if __name__=='__main__':main()
