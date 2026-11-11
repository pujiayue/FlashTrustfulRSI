"""Small credential-free worker control RPC for a frozen SearchQA campaign."""
import argparse,fcntl,hashlib,json,os,subprocess,sys,time
from pathlib import Path
from flashtrustfulrsi.provider import write_json

def main():
 p=argparse.ArgumentParser();p.add_argument('action',choices=['status','start']);p.add_argument('--plan',required=True);p.add_argument('--job-id');p.add_argument('--adopt',default='{}');a=p.parse_args()
 root=Path.cwd();plan=Path(a.plan).resolve();doc=json.loads(plan.read_text());phase=root/'runs'/doc['phase_id']
 if a.action=='status':
  adopted=json.loads(a.adopt);rows=[]
  for item in doc['jobs']:
   job=item['job_id'];dest=phase/job;path=dest/'status.json';state=json.loads(path.read_text()) if path.exists() else {'status':'planned'}
   launch=dest/'campaign-launch.json';pid=json.loads(launch.read_text())['pid'] if launch.exists() else adopted.get(job)
   if (dest/'result.json').exists():state['status']='completed'
   elif state.get('status')=='running' and pid:
    command=Path(f'/proc/{pid}/cmdline')
    if not command.exists() or job.encode() not in command.read_bytes():state={'status':'failed','error':'worker_process_missing_requires_reconciliation'}
   rows.append({'job_id':job,'state':state,'pid':pid})
  print(json.dumps({'phase':doc['phase_id'],'plan_sha256':hashlib.sha256(plan.read_bytes()).hexdigest(),'jobs':rows}));return
 job=next(j for j in doc['jobs'] if j['job_id']==a.job_id);dest=phase/job['job_id'];dest.mkdir(exist_ok=True)
 with (dest/'job.lock').open('a') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  if any((dest/n).exists() for n in ('campaign-launch.json','initial.json','status.json','result.json')):raise ValueError('Only untouched planned jobs may start through this RPC')
  audit=dest/'attempts/campaign-01';audit.mkdir(parents=True)
  command=[str(root/'.venv-native/bin/python'),'-B','scripts/resume_native_worker.py','--module','searchqa_formal','--audit',str(audit),
   'job','--root',str(root),'--plan',str(plan),'--job-id',job['job_id']]
  env=dict(os.environ,PYTHONPATH='src:upstream/RUC-NLPIR-FlashRSI-8567c65',PYTHONDONTWRITEBYTECODE='1')
  write_json(dest/'campaign-launch.json',{'pid':None,'status':'prepared','plan_sha256':hashlib.sha256(plan.read_bytes()).hexdigest(),'started':time.time()})
 with (audit/'process.log').open('ab') as log:process=subprocess.Popen(command,stdout=log,stderr=log,env=env,start_new_session=True)
 write_json(dest/'campaign-launch.json',{'pid':process.pid,'status':'launched','plan_sha256':hashlib.sha256(plan.read_bytes()).hexdigest(),'started':time.time()})
 print(json.dumps({'job_id':job['job_id'],'pid':process.pid}))

if __name__=='__main__':main()
