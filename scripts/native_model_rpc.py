"""Execute authorized native SDK calls only on the original credential host."""
import argparse,hashlib,json,re,sys
from pathlib import Path
from flashtrustfulrsi.native_transport import MODEL,RecordedNativeProvider


def main():
    p=argparse.ArgumentParser();p.add_argument('--root',required=True);p.add_argument('--job-id',required=True)
    p.add_argument('--phase-id',required=True);p.add_argument('--recovery-review');p.add_argument('--storage-recovery-review');a=p.parse_args();root=Path(a.root).resolve()
    if not all(re.fullmatch('[A-Za-z0-9_-]{1,80}',s) for s in (a.job_id,a.phase_id)):raise ValueError('Invalid identity')
    policy=json.loads((root/'configs/native-policies'/(a.phase_id+'.json')).read_text())
    if a.job_id not in policy['authorized_jobs'] or policy['model']!=MODEL:raise ValueError('Job absent from frozen policy')
    request=json.load(sys.stdin)
    digest=hashlib.sha256(json.dumps(request['body'],sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
    if digest!=request['body_sha256'] or request['job_id']!=a.job_id:raise ValueError('Identity/hash mismatch')
    if request['protocol'] not in policy['protocols'] or request['body'].get('model')!=MODEL or request['body'].get('stream'):
        raise ValueError('Outside frozen protocol/model policy')
    from flashtrustfulrsi.durable_transport import DurableProvider,install as install_durability
    install_durability();provider_class=DurableProvider
    if a.recovery_review and a.storage_recovery_review:raise ValueError('Only one recovery review allowed')
    if a.recovery_review:
        from flashtrustfulrsi.recovery import install
        from flashtrustfulrsi import native_transport
        install(a.recovery_review);provider_class=native_transport.RecordedNativeProvider
    if a.storage_recovery_review:
        from flashtrustfulrsi.storage_recovery import install
        from flashtrustfulrsi import native_transport
        install(a.storage_recovery_review);provider_class=native_transport.RecordedNativeProvider
    provider=provider_class(json.loads((root/'configs/provider-v02.json').read_text()),root/'runs'/a.phase_id/'provider',a.job_id,limits=policy['limits'])
    try:
        response=provider.invoke(request['protocol'],request['body'],request_id=request['call_id'])
        print(json.dumps({'ok':True,'body_sha256':digest,'response':response},ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({'ok':False,'error':type(exc).__name__+': '+str(exc)}));sys.exit(1)


if __name__=='__main__':main()
