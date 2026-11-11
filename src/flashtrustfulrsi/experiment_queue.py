"""Bounded durable job admission for frozen formal experiment manifests."""
import argparse,concurrent.futures,fcntl,json,subprocess,sys,time
from pathlib import Path
from .provider import write_json
from .artifacts import completed_jobs

MODULES={'dojo':'flashtrustfulrsi.dojo_formal','evopath':'flashtrustfulrsi.evopath_formal',
         'cim-labels':'flashtrustfulrsi.cim_formal','cim-evaluation':'flashtrustfulrsi.cim_evaluation','she':'flashtrustfulrsi.she_evolution',
         'she-guarded':'flashtrustfulrsi.she_guarded_evolution','cim-native-labels':'flashtrustfulrsi.cim_native_labels'}


def run(root,plan_path,module,workers):
    plan=json.loads(plan_path.read_text());dest=root/'runs'/plan['phase_id'];dest.mkdir(parents=True,exist_ok=True)
    if len(plan['jobs'])!=plan['job_count'] or len({j['job_id'] for j in plan['jobs']})!=plan['job_count']:raise ValueError('Invalid denominator')
    with (dest/'queue.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        finished=completed_jobs(dest)
        pending=[j for j in plan['jobs'] if j['job_id'] not in finished]
        completed=plan['job_count']-len(pending);index=0;active={};failed=[]
        def one(job):
            path=dest/job['job_id'];path.mkdir(exist_ok=True)
            command=([sys.executable,'-m',MODULES[module]] if module in {'she-guarded','cim-native-labels'} else
                [sys.executable,'-m','flashtrustfulrsi.durable_transport','--module',MODULES[module].rsplit('.',1)[-1]])
            with (path/'process.log').open('a') as log:
                return subprocess.run([*command,'job','--root',str(root),'--plan',str(plan_path),'--job-id',job['job_id']],stdout=log,stderr=log).returncode
        def save(status):
            write_json(dest/'queue-status.json',{'status':status,'completed':completed,'total':plan['job_count'],
                'active':list(active.values()),'failed':failed,'updated':time.time(),'workers':workers})
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            while active or (index<len(pending) and not failed):
                while not failed and len(active)<workers and index<len(pending):
                    job=pending[index];index+=1;active[pool.submit(one,job)]=job['job_id']
                save('draining_after_failure' if failed else 'running')
                done,_=concurrent.futures.wait(active,timeout=30,return_when=concurrent.futures.FIRST_COMPLETED)
                for future in done:
                    job=active.pop(future)
                    if future.result():failed.append(job)
                    else:completed+=1
            save('stopped_on_failure' if failed else 'completed')
        if failed:raise RuntimeError('Queue stopped; failed job evidence preserved')


def main():
    p=argparse.ArgumentParser();p.add_argument('--root',default='.');p.add_argument('--plan',required=True);p.add_argument('--module',choices=MODULES,required=True);p.add_argument('--workers',type=int,default=4)
    a=p.parse_args()
    if not 1<=a.workers<=8:raise ValueError('Worker count must be 1..8')
    run(Path(a.root).resolve(),Path(a.plan).resolve(),a.module,a.workers)


if __name__=='__main__':main()
