"""Trusting Trust native YAML behavioral witness, only inside verified Docker.

The host stages bytes but never imports or executes candidate Python solutions.
Unavailable Docker produces an explicit skipped result under the user's policy.
"""
import argparse, hashlib, json, re, shutil
from pathlib import Path
from .components import source
from .provider import write_json
from .sandbox import DockerSandbox


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',default='.')
    p.add_argument('--job-id',required=True);p.add_argument('--input-dir',required=True)
    p.add_argument('--image');p.add_argument('--docker',default='docker');p.add_argument('--docker-host')
    p.add_argument('--plan-only',action='store_true');a=p.parse_args()
    if not re.fullmatch('[a-zA-Z0-9_-]{1,80}',a.job_id):raise ValueError('Invalid job ID')
    root=Path(a.root).resolve();inputs=Path(a.input_dir).resolve(strict=True)
    candidates=sorted(inputs.glob('*.py'))
    if not candidates or len(candidates)>100:raise ValueError('Expected 1..100 Python candidate files')
    if any(p.is_symlink() or p.stat().st_size>1024*1024 for p in candidates):raise ValueError('No symlinks or files over 1 MiB')
    runner=source(root,'E08','evaluation/behavioral_witness/behavioral_yaml.py')
    spec={'method':'Trusting Trust Revisited','profile':'yaml_behavioral_witness','job_id':a.job_id,
          'scope':'native_behavioral_witness_not_evolution','paid_calls':0,
          'input_hashes':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in candidates},
          'runner_sha256':hashlib.sha256(runner.read_bytes()).hexdigest(),
          'image':a.image,'command':['python','/workspace/runner.py','--pydir','/workspace/cases'],
          'skip_policy':'user_requested_skip_unavailable_docker_2026-11-05',
          'limitations':['native safe classification does not prove general Python sandbox security',
                         'no self-modification chain or poisoned/clean twin training in this profile']}
    if a.plan_only:print(json.dumps(spec,indent=2));return
    dest=root/'runs'/a.job_id;dest.mkdir(parents=True,exist_ok=True)
    if (dest/'manifest.json').exists() and json.loads((dest/'manifest.json').read_text())!=spec:raise ValueError('Frozen manifest changed')
    write_json(dest/'manifest.json',spec)
    if (dest/'result.json').exists():print((dest/'result.json').read_text());return
    sandbox=DockerSandbox(a.docker,a.docker_host,a.image);report=sandbox.probe();write_json(dest/'sandbox.json',report)
    if report['status']!='ready':
        result={'status':'skipped_environment','scope':spec['scope'],'reason':report.get('reason'),
                'native_algorithm_executed':False,'host_fallback':False,'paid_calls':0}
    else:
        staging=dest/'workspace';staging.mkdir(exist_ok=True);cases=staging/'cases';cases.mkdir(exist_ok=True)
        shutil.copyfile(runner,staging/'runner.py')
        for candidate in candidates:shutil.copyfile(candidate,cases/candidate.name)
        for folder in (staging,cases):folder.chmod(0o755)
        for file in [staging/'runner.py',*cases.glob('*.py')]:file.chmod(0o644)
        output=sandbox.run(spec['command'],name='ftrsi-witness-'+hashlib.sha256(a.job_id.encode()).hexdigest()[:12],workdir=staging,timeout=60)
        (dest/'native-stdout.txt').write_text(output)
        match=re.search(r'UNSAFE\(unsafe_load\)=(\d+)\s+FULLLOADER=(\d+)\s+SAFE=(\d+)\s+unclear=(\d+)\s+\(n=(\d+)\)',output)
        if not match:raise ValueError('Native witness summary missing')
        counts=dict(zip(('unsafe','fullloader','safe','unclear','n'),map(int,match.groups())))
        if counts['n']!=len(candidates):raise ValueError('Native witness coverage mismatch')
        result={'status':'completed','scope':spec['scope'],'native_algorithm_executed':True,'counts':counts,'paid_calls':0}
    write_json(dest/'result.json',result);write_json(dest/'status.json',{'status':result['status']});print(json.dumps(result,indent=2))


if __name__=='__main__':main()
