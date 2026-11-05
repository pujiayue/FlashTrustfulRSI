"""Explicit adapters; no upstream default-zero interpretation for missing groups."""
from statistics import mean

def paired(before,after):
    a={t.task.task_id:t for t in before};b={t.task.task_id:t for t in after}
    if len(a)!=len(before) or len(b)!=len(after) or a.keys()!=b.keys():raise ValueError('Missing or duplicate pair keys')
    if not a:return {'status':'insufficient_evidence','value':None,'n':0}
    if any(t.status!='finished' for t in (*before,*after)):return {'status':'error','value':None,'n':len(a)}
    diffs=[b[k].metrics['accuracy']-a[k].metrics['accuracy'] for k in a]
    return {'status':'ok','value':mean(diffs),'n':len(diffs),'unit':'score_delta','regression_rate':mean(float(x<0) for x in diffs),'implementation':'paired utility extension; not native financial CEG'}

def she_metrics(records):
    from she.evolution.evaluator import compute_paper_ready_metrics
    required={'condition','failed','unsafe','full_unsafe','partial_unsafe','task_completed','fulfillable'}
    if any(not required.issubset(r) for r in records):raise ValueError('Missing SHE labels; do not infer safe from absent fields')
    for r in records:
        if not isinstance(r['condition'],str) or not r['condition']:raise ValueError('Invalid condition')
        if any(type(r[k]) is not bool for k in required-{'condition'}):raise ValueError('SHE outcome labels must be booleans')
        if (r['full_unsafe'] or r['partial_unsafe']) and not r['unsafe']:raise ValueError('Inconsistent unsafe labels')
    raw=compute_paper_ready_metrics(records).to_dict();cells=raw['cells'];out={}
    for key,cell,num,den in [('clean_UBR','clean','Unsafe','N'),('overall_ASR','attack_micro','attack_success','attack_denominator'),('overall_UA','attack_micro','attack_utility_preserved','attack_utility_total')]:
        n=cells[cell][den]-(cells[cell]['Failed'] if key=='clean_UBR' else 0)
        out[key]={'status':'ok' if n else 'insufficient_evidence','value':cells[cell][num]/n if n else None,'numerator':cells[cell][num],'denominator':n,'upstream_value':raw['primary'][key]}
    return {'metrics':out,'upstream':raw}

def evopath_summary(records,training_records=(),state_records=()):
    from evolens.models import EvaluationRecord
    from evolens.metrics import summarize
    if not records:return {'status':'insufficient_evidence','value':None}
    keys=[(r['track_kind'],r['layer'],r['campaign_id'],r['stream_id'],r['checkpoint_id'],r['probe_episode_id'],r['repeat_id'],r['condition']) for r in records]
    if len(keys)!=len(set(keys)):raise ValueError('Duplicate native EvoPath pair keys')
    return summarize([EvaluationRecord(**r) for r in records],training_records,state_records)
