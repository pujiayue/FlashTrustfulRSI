"""Bounded local orchestration of 147 workers and the original API host bridge."""
import argparse,fcntl,json,subprocess,sys,time
from pathlib import Path
from ssh_model_bridge import remote

def main():
 p=argparse.ArgumentParser()
 for name in ('worker-host','worker-root','worker-control-path','model-host','model-root','plan','phase','output'):p.add_argument('--'+name,required=True)
 p.add_argument('--workers',type=int,default=4);p.add_argument('--adopt',default='{}');a=p.parse_args()
 if not 1<=a.workers<=4:raise ValueError('Worker cap 1..4')
 output=Path(a.output).resolve();output.mkdir(parents=True,exist_ok=True);adopted=json.loads(a.adopt);bridges={};failed=[]
 def rpc(action,job=None):
  return remote(a.worker_host,a.worker_root,'.venv-native/bin/python','native_campaign_worker.py',
   [action,'--plan',a.plan,'--adopt',a.adopt]+(['--job-id',job] if job else []),control=a.worker_control_path)
 with (output/'campaign.lock').open('a') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  while True:
   state=rpc('status');rows=state['jobs'];completed=[r['job_id'] for r in rows if r['state']['status']=='completed'];active=[r['job_id'] for r in rows if r['state']['status']=='running']
   failed=list(dict.fromkeys([*failed,*[r['job_id'] for r in rows if r['state']['status']=='failed']]))
   for job,process in list(bridges.items()):
    if process.poll() is not None:
     if process.returncode and job not in completed:failed.append(job)
     del bridges[job]
   planned=[r['job_id'] for r in rows if r['state']['status']=='planned']
   while planned and len(active)<a.workers and not failed:
    job=planned.pop(0);rpc('start',job);active.append(job)
    cmd=[sys.executable,str(Path(__file__).with_name('ssh_model_bridge.py')),'--job-id',job,
     '--worker-host',a.worker_host,'--worker-root',a.worker_root,'--worker-python','.venv-native/bin/python','--worker-control-path',a.worker_control_path,
     '--model-host',a.model_host,'--model-root',a.model_root,'--native-phase-id',a.phase,'--worker-phase-id',a.phase,
     '--lock',str(output/(job+'.bridge.lock')),'--deadline-seconds','86400']
    with (output/(job+'.bridge.log')).open('ab') as log:bridges[job]=subprocess.Popen(cmd,stdout=log,stderr=log,start_new_session=True)
   status={'status':'draining_after_failure' if failed else 'completed' if len(completed)==len(rows) else 'running',
    'completed':len(completed),'total':len(rows),'active':active,'failed':failed,'adopted_jobs':list(adopted),'plan_sha256':state['plan_sha256'],'updated':time.time()}
   temp=output/'status.json.tmp';temp.write_text(json.dumps(status,indent=2));temp.replace(output/'status.json')
   if len(completed)==len(rows):return
   if failed and not active:raise RuntimeError('Campaign stopped; failed evidence retained')
   time.sleep(15)

if __name__=='__main__':main()
