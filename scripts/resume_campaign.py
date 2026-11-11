"""Resume explicitly reviewed failures, then return to the frozen full queue."""
import argparse,concurrent.futures,fcntl,json,subprocess,sys,time
from pathlib import Path
from flashtrustfulrsi.provider import write_json
from flashtrustfulrsi.artifacts import completed_jobs

MODULES={'evopath_formal':'evopath','dojo_formal':'dojo','cim_formal':'cim-labels','she_evolution':'she'}

def main():
 p=argparse.ArgumentParser();p.add_argument('--index',type=Path,required=True);p.add_argument('--module',choices=MODULES,required=True);p.add_argument('--workers',type=int,default=4)
 p.add_argument('--recovery-module',choices=['recovery','storage_recovery'],default='recovery');a=p.parse_args()
 if not 1<=a.workers<=4:raise ValueError('Recovery workers must be 1..4')
 rows=[r for r in json.loads(a.index.read_text()) if r['module']==a.module]
 if not rows:raise ValueError('No reviewed jobs')
 root=Path(rows[0]['root']);plan=Path(rows[0]['plan']);doc=json.loads(plan.read_text());dest=root/'runs'/doc['phase_id']
 if any(r['root']!=str(root) or r['plan']!=str(plan) for r in rows):raise ValueError('Mixed recovery campaigns')
 def one(row):
  path=Path(row['review']).parent
  command=([sys.executable,'-m','flashtrustfulrsi.storage_recovery'] if a.recovery_module=='storage_recovery' else
   [sys.executable,'-m','flashtrustfulrsi.durable_transport','--module','recovery','--'])
  with (path/'process.log').open('a') as log:
   return subprocess.run([*command,'--review',row['review'],'--module',a.module,
    'job','--root',str(root),'--plan',str(plan),'--job-id',row['job_id']],stdout=log,stderr=log).returncode
 with (dest/'queue.lock').open('a') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);failed=[];active={};index=0
  finished=completed_jobs(dest);rows=[r for r in rows if r['job_id'] not in finished]
  with concurrent.futures.ThreadPoolExecutor(max_workers=a.workers) as pool:
   while active or (index<len(rows) and not failed):
    while len(active)<a.workers and index<len(rows) and not failed:
     row=rows[index];index+=1;active[pool.submit(one,row)]=row['job_id']
    completed=len(completed_jobs(dest))
    write_json(dest/'queue-status.json',{'status':'recovery_draining_after_failure' if failed else 'reviewed_recovery_running',
     'completed':completed,'total':doc['job_count'],'active':list(active.values()),'failed':failed,'updated':time.time(),'workers':a.workers,'review_index':str(a.index)})
    done,_=concurrent.futures.wait(active,timeout=30,return_when=concurrent.futures.FIRST_COMPLETED)
    for future in done:
     job=active.pop(future)
     if future.result():failed.append(job)
  if failed:
   write_json(dest/'queue-status.json',{'status':'recovery_stopped_on_failure','completed':len(completed_jobs(dest)),
    'total':doc['job_count'],'active':[],'failed':failed,'updated':time.time()})
   raise RuntimeError('Reviewed recovery failed; no automatic retry')
 # New jobs use the original frozen transport; all recovered jobs have result.json.
 subprocess.run([sys.executable,'-m','flashtrustfulrsi.experiment_queue','--root',str(root),'--plan',str(plan),'--module',MODULES[a.module],'--workers',str(a.workers)],check=True)

if __name__=='__main__':main()
