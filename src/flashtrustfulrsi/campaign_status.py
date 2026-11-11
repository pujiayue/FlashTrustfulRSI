"""Read-only campaign status, including verified archived jobs."""
import json,sqlite3
from collections import Counter
from pathlib import Path

def state(document):
    value=document.get('status','unknown')
    # Native Dojo status uses completed + execution_error, while its scientific
    # result uses completed_with_execution_error. Preserve that distinction.
    return 'completed_with_execution_error' if value=='completed' and document.get('execution_error') else value

def summarize(phase):
    phase=Path(phase);states={};index=phase/'completed-index.sqlite3'
    if index.exists():
        db=sqlite3.connect(f'file:{index}?mode=ro',uri=True)
        try:
            for job,status in db.execute('SELECT job,status FROM jobs'):
                states[job]=state(json.loads(status))
        finally:db.close()
    archived=len(states)
    for path in phase.glob('*/status.json'):
        states[path.parent.name]=state(json.loads(path.read_text()))
    # Successful job result is authoritative when a status file is absent/stale.
    for path in phase.glob('*/result.json'):
        status=json.loads(path.read_text()).get('status')
        if status in {'completed','completed_with_execution_error'}:states[path.parent.name]=status
    counts=Counter(states.values());q=phase/'queue-status.json'
    queue=json.loads(q.read_text()) if q.exists() else {}
    completed=counts['completed']+counts['completed_with_execution_error']
    return {'kind':'campaign','phase_id':phase.name,'states':dict(counts),'completed_jobs':completed,
        'completed_without_execution_error':counts['completed'],'execution_error_jobs':counts['completed_with_execution_error'],
        'archived_jobs':archived,'observed_jobs':len(states),'planned_total':queue.get('total'),
        'queue_status':queue.get('status'),'queue_completed_matches_evidence':queue.get('completed')==completed if 'completed' in queue else None,
        'note':'Job counts are not utility/ASR denominators; running state is last recorded, not a process liveness probe.'}

def inventory(root):
    rows=[]
    for phase in sorted((Path(root)/'runs').iterdir() if (Path(root)/'runs').exists() else []):
        if not phase.is_dir():continue
        if (phase/'queue-status.json').exists() or (phase/'completed-index.sqlite3').exists():rows.append(summarize(phase))
        elif (phase/'manifest.json').exists():
            status=phase/'status.json';result=phase/'result.json'
            rows.append({'kind':'job','job_id':phase.name,'status':json.loads(status.read_text()) if status.exists() else {'status':'planned'},'result':str(result) if result.exists() else None})
    return rows
