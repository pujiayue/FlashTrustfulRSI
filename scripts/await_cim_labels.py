"""Run frozen CIM target evaluation only after all label jobs finish cleanly."""
import argparse,fcntl,json,subprocess,sys,time
from pathlib import Path
from flashtrustfulrsi.provider import write_json

def main():
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--labels-plan',type=Path,required=True);p.add_argument('--plan',type=Path,required=True);a=p.parse_args()
    a.root=a.root.resolve();a.labels_plan=a.labels_plan.resolve();a.plan=a.plan.resolve()
    labels=json.loads(a.labels_plan.read_text());plan=json.loads(a.plan.read_text());dest=a.root/'runs'/plan['phase_id'];dest.mkdir(parents=True,exist_ok=True)
    with (dest/'dependency.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        while True:
            parent=a.root/'runs'/labels['phase_id'];ready=sum((parent/j['job_id']/'result.json').exists() for j in labels['jobs'])
            status=json.loads((parent/'queue-status.json').read_text()) if (parent/'queue-status.json').exists() else {}
            write_json(dest/'dependency-status.json',{'status':'waiting_for_all_frozen_labels','ready_labels':ready,'total_labels':labels['job_count'],'updated':time.time()})
            if ready==labels['job_count']:break
            if status.get('failed'):
                write_json(dest/'dependency-status.json',{'status':'blocked_by_label_failure','failed':status['failed'],'updated':time.time()})
                raise RuntimeError('Label failure requires explicit reconciliation; no target calls made')
            time.sleep(30)
        subprocess.run([sys.executable,'-m','flashtrustfulrsi.cim_formal','assemble','--root',str(a.root),'--plan',str(a.labels_plan)],check=True)
        write_json(dest/'dependency-status.json',{'status':'labels_frozen_evaluation_started','updated':time.time()})
        subprocess.run([sys.executable,'-m','flashtrustfulrsi.experiment_queue','--root',str(a.root),'--plan',str(a.plan),'--module','cim-evaluation','--workers','4'],check=True)

if __name__=='__main__':main()
