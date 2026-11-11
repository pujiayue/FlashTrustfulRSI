"""SHE variant: transactional native patch validation with invalid-candidate rejection.

An invalid proposal consumes its scheduled attempt, leaves the deployed harness
unchanged, and is not repaired or resampled. Original v04 failed runs are retained.
"""
import argparse,json,shutil,time
from pathlib import Path
from . import she_evolution as native
from .durable_transport import DurableProvider
from .provider import write_json


def transactional_patch(script,argv,runner):
    args=list(map(str,argv));active=Path(args[args.index('--artifacts-dir')+1]);rd=Path(args[args.index('--round-dir')+1])
    before=native.file_hashes(active);staged=rd/'staged-artifacts'
    if staged.exists():raise ValueError('Partial proposal staging requires reconciliation')
    shutil.copytree(active,staged);args[args.index('--artifacts-dir')+1]=str(staged)
    try:
        runner(script,args)
    except ValueError as exc:
        # Only failures after a model proposal was actually persisted qualify as
        # candidate rejection. Transport, missing dependencies and I/O remain fatal.
        raw=rd/'evolve_agent_raw.txt'
        if not raw.is_file():raise
        from she.evolution.manifest import write_manifests
        if native.file_hashes(active)!=before:raise RuntimeError('Rejected proposal changed active artifacts')
        write_manifests(rd/'change_manifest.json',[],author='transactional-native-rejection',
            verification_status='rejected',verification_method='native_schema_or_persistence_gate')
        write_json(rd/'transaction.json',{'status':'rejected','reason':str(exc),'error_type':type(exc).__name__,
            'proposal_sha256':native.sha(raw),'before':before,'after':before,'changed':False,'retry_or_repair':False,'time':time.time()})
        return
    after=native.file_hashes(staged)
    if native.file_hashes(active)!=before:raise RuntimeError('Active artifacts changed during proposal staging')
    # The native patcher has already validated paths and schema in the staging
    # copy. Preserve a rollback copy; publish only after successful completion.
    backup=rd/'pre-proposal-artifacts';shutil.copytree(active,backup)
    for path in active.iterdir():
        if path.is_symlink():raise ValueError('Unexpected active artifact symlink')
    shutil.rmtree(active);shutil.copytree(staged,active)
    if native.file_hashes(active)!=after:raise RuntimeError('Artifact commit verification failed')
    write_json(rd/'transaction.json',{'status':'valid_applied','before':before,'after':after,'changed':before!=after,
        'retry_or_repair':False,'time':time.time()})


def make_plan(root,phase,reuse_phase=None):
    frozen=native.plan(root,phase);frozen['scope']='T6_SHE_transactional_invalid_proposal_rejection_on_AgentDojo_not_original_ASB'
    frozen['schema']='flashtrustfulrsi.formal.she-dojo-transactional.v1'
    frozen['adapter_files']['she_guarded_evolution.py']=native.sha(Path(__file__))
    frozen['adapter_files']['durable_transport.py']=native.sha(Path(__file__).with_name('durable_transport.py'))
    frozen['invalid_candidate_policy']='native validator rejection consumes one proposal; unchanged harness proceeds; no repair/resampling'
    frozen['deviations']+=['separate v04b condition: transactional patch application and invalid-proposal rejection, original v04 failures retained',
        '20 scheduled proposals, not 20 guaranteed successful updates; count rejected, valid no-op, and changed proposals separately',
        'provider response inode reservations and persistent WAL anchor; no change to model payload']
    reused={}
    if reuse_phase:
        for job in frozen['jobs']:
            old=root/'runs'/reuse_phase/job['job_id'];round0=old/'rounds/R00';records=round0/'trajectories.jsonl'
            if not records.exists():continue
            expected=len(job['train'])*frozen['training_rollouts'];lines=[json.loads(s) for s in records.read_text().splitlines()]
            if len(lines)!=expected or any(r['failed'] for r in lines):raise ValueError('Incomplete or failed initial observation cohort')
            manifest=json.loads((old/'manifest.json').read_text())
            if manifest['job']!=job:raise ValueError('Initial cohort inputs differ')
            rollouts=round0/'rollouts';files=native.file_hashes(rollouts)
            for ri,case in enumerate(job['train']):
                for repeat in range(frozen['training_rollouts']):
                    identity=f'R00-c{ri:04d}-r{repeat}';inputdoc=json.loads((rollouts/identity/'input.json').read_text())
                    up=next((root/'upstream').glob('RainbowQTT-SHE-*'))
                    if inputdoc!={'case':case,'seed':job['seed'],'artifacts':native.file_hashes(up/'artifacts')}:raise ValueError('Initial rollout fingerprint mismatch')
            reused[job['job_id']]={'source_phase':reuse_phase,'source':str(rollouts),'files_sha256':files,
                'source_manifest_sha256':native.sha(old/'manifest.json'),'observations':expected,
                'usage_note':'Shared original seed observations, not independent new samples. Original provider costs remain in source run.'}
    frozen['reused_seed_observations']=reused
    return frozen


def run_job(root,plan_path,job_id):
    frozen=json.loads(plan_path.read_text());dest=root/'runs'/frozen['phase_id']/job_id
    if native.sha(Path(__file__))!=frozen['adapter_files']['she_guarded_evolution.py']:raise ValueError('Frozen wrapper changed')
    reuse=frozen['reused_seed_observations'].get(job_id)
    if reuse and not (dest/'seed-observation-reuse.json').exists():
        if (dest/'started.json').exists():raise ValueError('Partial evolution requires reconciliation before observation reuse')
        source=Path(reuse['source'])
        if native.file_hashes(source)!=reuse['files_sha256']:raise ValueError('Original seed evidence changed')
        shutil.copytree(source,dest/'rounds/R00/rollouts')
        write_json(dest/'seed-observation-reuse.json',reuse)
    runner=native.native_script
    def execute(script,argv):
        if Path(script).name=='propose_artifact_patch.py':return transactional_patch(script,argv,runner)
        return runner(script,argv)
    previous_provider=native.RecordedNativeProvider
    native.native_script=execute;native.RecordedNativeProvider=DurableProvider
    try:native.run_job(root,plan_path,job_id)
    finally:native.native_script=runner;native.RecordedNativeProvider=previous_provider
    path=dest/'result.json';result=json.loads(path.read_text());transactions=[]
    for rd in sorted((dest/'rounds').glob('R*')):
        if (rd/'transaction.json').exists():transactions.append({'round':rd.name,**json.loads((rd/'transaction.json').read_text())})
    if len(transactions)!=frozen['updates']:raise ValueError('Proposal attempt denominator differs')
    result.update(scheduled_proposals=frozen['updates'],updates=sum(t['changed'] for t in transactions),
        updates_semantics='proposed artifact changes applied before next-round selection; not guaranteed selected improvements',
        rejected_proposals=sum(t['status']=='rejected' for t in transactions),valid_noop_proposals=sum(t['status']=='valid_applied' and not t['changed'] for t in transactions),
        patch_transactions=transactions,shared_seed_observations=reuse)
    write_json(path,result)


def main():
    p=argparse.ArgumentParser();p.add_argument('action',choices=['plan','job']);p.add_argument('--root',default='.');p.add_argument('--plan',required=True)
    p.add_argument('--phase',default='formal-she-dojo-v04b');p.add_argument('--reuse-phase');p.add_argument('--job-id');a=p.parse_args()
    root=Path(a.root).resolve();path=Path(a.plan).resolve()
    if a.action=='plan':
        doc=json.loads(json.dumps(make_plan(root,a.phase,a.reuse_phase)))
        if path.exists() and json.loads(path.read_text())!=doc:raise ValueError('Frozen plan changed')
        write_json(path,doc);print(json.dumps({'jobs':doc['job_count'],'proposals_per_job':doc['updates'],'shared_seed_cohorts':len(doc['reused_seed_observations'])}))
    else:run_job(root,path,a.job_id)

if __name__=='__main__':main()
