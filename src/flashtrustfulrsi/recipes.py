"""Immutable developer recipes over original protocols; no formal experiment launcher."""
import argparse
import fcntl
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
from .development import ENTRIES, FORBIDDEN
from .deepseek_debug import ledger_status
from .provider import write_json

SCHEMA='flashtrustfulrsi.recipe.v1'
PREPARED='flashtrustfulrsi.recipe.prepared.v1'
OFFICIAL_ENDPOINTS={'https://api.deepseek.com/chat/completions','https://api.deepseek.com/anthropic/v1/messages'}


def digest(path):
    path=Path(path)
    if path.is_symlink():raise ValueError('Recipe inputs cannot be symbolic links')
    if path.is_dir():
        entries=[]
        for child in sorted(path.rglob('*')):
            if child.is_symlink():raise ValueError('Recipe input directory contains a symbolic link')
            if child.is_file():entries.append((str(child.relative_to(path)),sha256(child.read_bytes()).hexdigest()))
        return sha256(json.dumps(entries,separators=(',',':')).encode()).hexdigest()
    return sha256(path.read_bytes()).hexdigest()


def package_fingerprint():
    return {p.name:digest(p) for p in sorted(Path(__file__).parent.glob('*.py'))}


def load_recipe(path):
    path=Path(path)
    if path.stat().st_size>65536:raise ValueError('Developer recipe exceeds 64KiB')
    spec=json.loads(path.read_text())
    allowed={'schema','scope','model','formal_data_used','profile','parameters','input_sha256','max_wall_seconds'}
    if (spec.keys()-allowed or spec.get('schema')!=SCHEMA or spec.get('scope')!='development_only'
            or spec.get('model')!='deepseek-flash' or spec.get('formal_data_used') is not False):
        raise ValueError('Explicit official developer recipe required; formal recipes are not accepted')
    if spec.get('profile') not in {*ENTRIES,'compose'}:raise ValueError('Unknown developer protocol')
    parameters=spec.get('parameters')
    if not isinstance(parameters,dict) or not parameters:raise ValueError('Explicit protocol parameters required')
    flags=[];normalized={}
    blocked=FORBIDDEN|{'--root','--plan-only','--preflight','--inside','--output','--deepseek-debug'}
    for key,value in parameters.items():
        if not re.fullmatch(r'[a-z][a-z0-9_-]*',key):raise ValueError('Invalid protocol parameter name')
        flag='--'+key.replace('_','-')
        if flag in blocked or flag in normalized:raise ValueError('Reserved or duplicate protocol parameter: '+flag)
        if type(value) not in (str,int,float,bool):raise ValueError('Protocol values must be explicit scalar values')
        if isinstance(value,str) and (not value or value.startswith('--') or len(value)>8192):raise ValueError('Invalid protocol argument value')
        normalized[flag]=value
        if type(value) is bool:
            if value:flags.append(flag)
        else:flags.extend([flag,str(value)])
        if flag in ('--image','--witness-image') and not re.fullmatch(r'sha256:[a-f0-9]{64}',str(value)):
            raise ValueError('Recipe images must be immutable local image IDs')
    # Canonical serialization also rejects NaN and infinities.
    json.dumps(spec,allow_nan=False)
    if spec['profile']=='sica':
        job=str(normalized.get('--pair-id',''))+'-'+str(normalized.get('--arm',''))
    else:job=normalized.get('--job-id')
    if not isinstance(job,str) or not re.fullmatch(r'dev-dsflash-[a-z0-9-]{1,100}',job):
        raise ValueError('A canonical dedicated developer job identity is required')
    wall=spec.get('max_wall_seconds',14400)
    if type(wall) is not int or not 30<=wall<=14400:raise ValueError('Explicit bounded wall time must be 30..14400 seconds')
    if not isinstance(spec.get('input_sha256',{}),dict):raise ValueError('input_sha256 must map file paths to hashes')
    return spec,flags,job


def command(spec,flags,root,action):
    if spec['profile']=='compose':return ['-m','flashtrustfulrsi','compose',action,'--root',str(root),*flags]
    return ['-m','flashtrustfulrsi','debug',action,'--root',str(root),'--profile',spec['profile'],*flags]


def protocol_plan(spec,flags,root,runner=None):
    args=command(spec,flags,root,'plan')
    if runner is None:
        result=subprocess.run([sys.executable,*args],cwd=root,capture_output=True,text=True,timeout=120)
    else:result=runner(args)
    if result.returncode:raise ValueError('Native protocol preflight failed: '+result.stderr[-2000:])
    if len(result.stdout.encode())>4*1024*1024:raise ValueError('Native protocol plan exceeds the output limit')
    try:plan=json.loads(result.stdout)
    except json.JSONDecodeError as exc:raise ValueError('Native plan did not emit one JSON object') from exc
    model=plan.get('model',plan.get('settings',{}).get('model'))
    if model!='deepseek-flash' or plan.get('runs_formal_eval') is not False:
        raise ValueError('Native plan did not confirm the official development model and non-formal scope')
    return plan


def prepare(recipe,root,output,runner=None):
    root=Path(root).resolve();output=Path(output).resolve();spec,flags,job=load_recipe(recipe)
    if output.exists():raise FileExistsError('Prepared recipe already exists; choose a new file')
    if (root/'runs'/job).exists():raise FileExistsError('Native job already exists; use explicit protocol recovery')
    inputs={}
    for name,expected in spec.get('input_sha256',{}).items():
        file=Path(name);file=(root/file).resolve() if not file.is_absolute() else file.resolve()
        if digest(file)!=expected:raise ValueError('Explicit recipe input changed: '+name)
        inputs[str(file)]=expected
    for key in ('data','source_trace','review','config'):
        value=spec['parameters'].get(key,spec['parameters'].get(key.replace('_','-')))
        if value:
            file=(root/value).resolve();inputs[str(file)]=digest(file)
    plan=protocol_plan(spec,flags,root,runner)
    frozen={'schema':PREPARED,'root':str(root),'recipe':spec,'job_id':job,'argv':flags,
            'input_sha256':inputs,'native_plan':plan,'package_fingerprint':package_fingerprint(),
            'recipe_sha256':digest(recipe),'model_requests_during_prepare':0,'runs_formal_eval':False}
    output.parent.mkdir(parents=True,exist_ok=True)
    with output.open('x') as stream:json.dump(frozen,stream,indent=2,ensure_ascii=False,allow_nan=False)
    output.chmod(0o600)
    return frozen


def collect_native(root,job):
    run=Path(root)/'runs'/job;status_path=run/'status.json';result_path=run/'result.json'
    status=json.loads(status_path.read_text()) if status_path.exists() else {'status':'missing_native_status'}
    ledger_rows=[];unresolved=[]
    manifest_path=run/'manifest.json'
    manifest=json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    sica_resume=bool(manifest.get('committed_resume')) and str(manifest.get('scope','')).startswith('development_only_sica_')
    for path in sorted(run.rglob('deepseek-debug.sqlite3')):
        value=ledger_status(path.parent)
        if value['model']!='deepseek-flash' or value['endpoint'] not in OFFICIAL_ENDPOINTS:raise ValueError('Unexpected native provider route')
        if any(row['state']!='success' for row in value['calls']):unresolved.append(str(path))
        relative=str(path.relative_to(run))
        reused=bool(sica_resume and re.match(r'^evolution/evaluations/[01]/',relative))
        ledger_rows.append({'path':relative,'requests':value['requests'],
                            'provenance':'reused_committed_source' if reused else 'new_job',
                            'states':sorted({row['state'] for row in value['calls']})})
    if manifest_path.exists():
        model=manifest.get('model',manifest.get('settings',{}).get('model'))
        if model!='deepseek-flash' or manifest.get('runs_formal_eval') is not False:raise ValueError('Native manifest is not official developer scope')
    result=json.loads(result_path.read_text()) if result_path.exists() else {}
    completed=(status['status']=='completed' and result.get('status')=='completed' and manifest_path.exists()
               and bool(ledger_rows) and not unresolved)
    return {'native_status':status,'native_result_sha256':digest(result_path) if result_path.exists() else None,
            'native_manifest_sha256':digest(manifest_path) if manifest_path.exists() else None,
            'ledger_summaries':ledger_rows,
            'physical_receipts':sum(r['requests'] for r in ledger_rows if r['provenance']=='new_job'),
            'reused_source_receipts':sum(r['requests'] for r in ledger_rows if r['provenance']=='reused_committed_source'),
            'unsettled_or_failed_ledgers':unresolved,'accepted_development_completion':completed}


def run_prepared(path,runner=None):
    path=Path(path).resolve();spec=json.loads(path.read_text())
    if spec.get('schema')!=PREPARED or spec.get('runs_formal_eval') is not False:raise ValueError('Expected prepared developer recipe')
    root=Path(spec['root']);job=spec['job_id']
    if package_fingerprint()!=spec['package_fingerprint']:raise ValueError('Library code changed after preparation; prepare a new recipe')
    for name,expected in spec['input_sha256'].items():
        if digest(name)!=expected:raise ValueError('Recipe input changed after preparation: '+name)
    control=root/'runs/recipe-control'/job;control.mkdir(parents=True,exist_ok=True)
    with (control/'run.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        receipt_path=control/'receipt.json'
        if receipt_path.exists():
            previous=json.loads(receipt_path.read_text())
            if previous['prepared_sha256']!=digest(path):raise ValueError('Prepared recipe identity changed')
            now=collect_native(root,job)
            if any(previous.get(k)!=v for k,v in now.items() if k!='accepted_development_completion'):
                raise ValueError('Committed native result changed; inspect without resubmitting')
            return {**previous,'idempotent_read':True,'new_model_requests':0}
        if (root/'runs'/job).exists():raise ValueError('Native job already started outside this recipe; reconcile it instead of rerunning')
        if protocol_plan(spec['recipe'],spec['argv'],root,runner)!=spec['native_plan']:
            raise ValueError('Native configuration, source or data changed after preparation')
        write_json(control/'invocation.json',{'prepared_path':str(path),'prepared_sha256':digest(path),
                   'command':[sys.executable,*command(spec['recipe'],spec['argv'],root,'run')],'status':'starting'})
        timed_out=False
        with (control/'stdout.log').open('w') as out,(control/'stderr.log').open('w') as err:
            if runner is None:
                try:
                    proc=subprocess.Popen([sys.executable,*command(spec['recipe'],spec['argv'],root,'run')],cwd=root,
                                          stdout=out,stderr=err,start_new_session=True)
                    exit_code=proc.wait(timeout=spec['recipe'].get('max_wall_seconds',14400))
                except subprocess.TimeoutExpired:
                    # Only this invocation's process group. Containers/RPC requests still require receipt reconciliation.
                    try:os.killpg(proc.pid,signal.SIGKILL)
                    except ProcessLookupError:pass
                    proc.wait();exit_code=None;timed_out=True
            else:
                proc=runner(command(spec['recipe'],spec['argv'],root,'run'));exit_code=proc.returncode
                out.write(proc.stdout);err.write(proc.stderr)
        native=collect_native(root,job)
        receipt={'schema':'flashtrustfulrsi.recipe.receipt.v1','prepared_sha256':digest(path),'job_id':job,
                 'profile':spec['recipe']['profile'],'root':str(root),'exit_code':exit_code,'wrapper_timeout':timed_out,
                 **native,'runs_formal_eval':False,'resubmission_policy':'No automatic retry; use protocol-specific recovery after reconciliation.'}
        receipt['package_unchanged_during_run']=package_fingerprint()==spec['package_fingerprint']
        receipt['accepted_development_completion']=(native['accepted_development_completion'] and exit_code==0
            and not timed_out and receipt['package_unchanged_during_run'])
        write_json(receipt_path,receipt)
        return receipt


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=('prepare','run','inspect'))
    p.add_argument('--root',default='.');p.add_argument('--recipe',type=Path);p.add_argument('--output',type=Path);p.add_argument('--prepared',type=Path)
    args=p.parse_args(argv)
    if args.action=='prepare':
        if not args.recipe or not args.output:p.error('--recipe and --output required')
        result=prepare(args.recipe,args.root,args.output)
    else:
        if not args.prepared:p.error('--prepared required')
        if args.action=='run':result=run_prepared(args.prepared)
        else:
            frozen=json.loads(args.prepared.read_text())
            result={'prepared':frozen,'native':collect_native(frozen['root'],frozen['job_id'])
                    if (Path(frozen['root'])/'runs'/frozen['job_id']).exists() else None}
    print(json.dumps(result,indent=2,ensure_ascii=False))
    if args.action=='run' and not result['accepted_development_completion']:raise SystemExit(2)
if __name__=='__main__':main()
