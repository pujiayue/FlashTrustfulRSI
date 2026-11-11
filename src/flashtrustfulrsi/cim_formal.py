"""Full released CIMemories label preparation, isolated from target evaluation.

Labels are a shared frozen measurement resource, not an RSI result. The source
prompt, three personas, ten trials and mixture weights are retained. All labels
are newly generated with the declared model; no human-gold claim is made.
"""
import argparse,fcntl,hashlib,json,time
from collections import Counter
from pathlib import Path
from textwrap import dedent
from types import SimpleNamespace
from .cim_live import load_functions,strict_json
from .native_transport import MODEL,RecordedNativeProvider
from .provider import write_json

COMMIT='e39806e92ec66db120c0f3b73c9cb105e7ff5314'
WEIGHTS=(.26,.64,.10)


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def mix_labels(statements,labels):
    statements=set(statements)
    if len(labels)!=3 or any(len(trials)!=10 for trials in labels):raise ValueError('Incomplete persona/trial denominator')
    distributions=[]
    for trials in labels:
        counts={s:Counter(necessary=0,private=0,unlabeled=0) for s in sorted(statements)}
        for label in trials:
            if set(label)!={'share','private'} or any(not isinstance(label[k],list) or any(not isinstance(s,str) for s in label[k]) for k in label):raise ValueError('Malformed persona label')
            share=set(label['share']);private=set(label['private'])
            if (share|private)-statements:raise ValueError('Persona changed source memory wording')
            # Original code gives share precedence if a statement is in both.
            for s in statements:counts[s]['necessary' if s in share else 'private' if s in private else 'unlabeled']+=1
        distributions.append({s:{k:v/10 for k,v in c.items()} for s,c in counts.items()})
    return {s:{k:sum(w*d[s][k] for w,d in zip(WEIGHTS,distributions)) for k in ('private','necessary','unlabeled')} for s in sorted(statements)}


def plan(root,phase):
    up=next((root/'upstream').glob('facebookresearch-CIMemories-*'))
    data=json.loads((up/'data/data_openai_gpt-oss-120b.json').read_text())
    jobs=[{'job_id':f'cim-label-u{ui:02d}-c{ci:03d}','user_index':ui,'context_index':ci,
        'attributes':len(profile['information_attributes']),
        'context_sha256':hashlib.sha256(json.dumps(context,sort_keys=True,ensure_ascii=False).encode()).hexdigest()}
        for ui,profile in enumerate(data) for ci,context in enumerate(profile['contexts'])]
    return {'schema':'flashtrustfulrsi.formal.cim-labels.v1','phase_id':phase,'model':MODEL,'upstream_commit':COMMIT,
        'scope':'T5_measurement_resource_only_not_evolution_result','users':len(data),'contexts':len(jobs),
        'personas':3,'trials_per_persona':10,'weights':WEIGHTS,'label_seed_base':20261105,'max_output_tokens':16384,
        'job_count':len(jobs),'jobs':jobs,'total_requests':len(jobs)*30,
        'limits_per_job':{'requests':30,'input_tokens':6_000_000,'output_tokens':30*16384},
        'source_files':{name:sha(up/name) for name in ('data/data_openai_gpt-oss-120b.json','data/persona_gold_labels.py','eval/eval.py','eval/prompts.yaml','eval/metrics.py')},
        'adapter_files':{name:sha(Path(__file__).with_name(name)) for name in ('cim_formal.py','cim_live.py','native_transport.py')},
        'labels':'new_model_generated_not_human_gold','human_review':'pending','judge_independent':False,
        'failure_policy':'stop on parse/transport failure, retain receipt, never fill zero/missing labels',
        'deviations':['gpt-5.6-sol replaces upstream gpt-5 labeling model','serial trials within each context, bounded parallel contexts',
            'all 30 trials required; upstream silently drops malformed JSON, this adapter fails explicitly',
            'explicit output cap and seed; label preparation separate from target evaluation']}


def run_job(root,plan_path,job_id):
    frozen=json.loads(plan_path.read_text());case=next(j for j in frozen['jobs'] if j['job_id']==job_id)
    up=next((root/'upstream').glob('facebookresearch-CIMemories-*'))
    for name,digest in frozen['source_files'].items():
        if sha(up/name)!=digest:raise ValueError('Frozen source changed: '+name)
    for name,digest in frozen['adapter_files'].items():
        if sha(Path(__file__).with_name(name))!=digest:raise ValueError('Frozen adapter changed: '+name)
    profile=json.loads((up/'data/data_openai_gpt-oss-120b.json').read_text())[case['user_index']]
    context=profile['contexts'][case['context_index']]
    if hashlib.sha256(json.dumps(context,sort_keys=True,ensure_ascii=False).encode()).hexdigest()!=case['context_sha256']:raise ValueError('Context changed')
    ns=load_functions(up/'data/persona_gold_labels.py',{'label_attributes'},{'dedent':dedent,'completion_cost':lambda r:0},assignments=('personas',))
    dest=root/'runs'/frozen['phase_id']/job_id;dest.mkdir(parents=True,exist_ok=True)
    with (dest/'job.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        manifest={'plan_sha256':sha(plan_path),**case}
        if (dest/'manifest.json').exists() and json.loads((dest/'manifest.json').read_text())!=manifest:raise ValueError('Job changed')
        write_json(dest/'manifest.json',manifest)
        if (dest/'result.json').exists():return
        write_json(dest/'status.json',{'status':'running','phase':'persona-labels','started':time.time()})
        provider=RecordedNativeProvider(json.loads((root/'configs/provider-v02.json').read_text()),dest/'provider',job_id,limits=frozen['limits_per_job'])
        labels=[];raw=[]
        try:
            for pi,persona in enumerate(ns['personas']):
                class LabelModel:
                    def raw(self,prompt,n=1):
                        responses=[]
                        for ti in range(n):
                            r=provider.invoke('chat',{'model':MODEL,'messages':[{'role':'user','content':prompt}],
                                'seed':frozen['label_seed_base']+pi*10+ti,'max_tokens':frozen['max_output_tokens']},request_id=f'label-p{pi}-t{ti}')
                            choice=r['choices'][0]
                            raw.append({'persona':pi,'trial':ti,'prompt':prompt,'response':r})
                            write_json(dest/'raw-labels.json',raw)
                            if choice.get('finish_reason')!='stop':raise ValueError('Incomplete label completion')
                            responses.append(SimpleNamespace(choices=[SimpleNamespace(message={'content':choice['message']['content']})]))
                        return responses
                texts,_=ns['label_attributes'](LabelModel(),persona,profile,context,n=10)
                labels.append([strict_json(t) for t in texts])
                write_json(dest/'parsed-labels.json',labels)
            mixed=mix_labels([v['memory_statement'] for v in profile['information_attributes'].values()],labels)
            write_json(dest/'frozen-labels.json',mixed)
            write_json(dest/'result.json',{'status':'completed','scope':frozen['scope'],**case,'trials':30,
                'labels_sha256':sha(dest/'frozen-labels.json'),'labels_source':frozen['labels'],'human_review':'pending','usage':provider.summary()})
            write_json(dest/'status.json',{'status':'completed','finished':time.time()})
        except Exception as exc:
            write_json(dest/'status.json',{'status':'failed','error_type':type(exc).__name__,'error':str(exc),'usage':provider.summary(),'finished':time.time()});raise


def assemble(root,plan_path):
    frozen=json.loads(plan_path.read_text());up=next((root/'upstream').glob('facebookresearch-CIMemories-*'))
    if sha(up/'data/data_openai_gpt-oss-120b.json')!=frozen['source_files']['data/data_openai_gpt-oss-120b.json']:raise ValueError('Source changed')
    data=json.loads((up/'data/data_openai_gpt-oss-120b.json').read_text());receipts=[]
    for case in frozen['jobs']:
        dest=root/'runs'/frozen['phase_id']/case['job_id'];result=json.loads((dest/'result.json').read_text())
        if result['status']!='completed' or result['labels_sha256']!=sha(dest/'frozen-labels.json'):raise ValueError('Incomplete/changed labels')
        data[case['user_index']]['contexts'][case['context_index']]['labels_combined']=json.loads((dest/'frozen-labels.json').read_text())
        receipts.append({'job_id':case['job_id'],'labels_sha256':result['labels_sha256']})
    target=root/'runs'/frozen['phase_id']/'frozen';target.mkdir(exist_ok=True)
    write_json(target/'profiles.json',data)
    write_json(target/'manifest.json',{'plan_sha256':sha(plan_path),'profiles_sha256':sha(target/'profiles.json'),'contexts':len(receipts),'jobs':receipts,
        'label_source':'new_gpt-5.6-sol_labels','human_review':'pending','license':'upstream CC BY-NC; external research data, not included in core Apache license'})


def main():
    p=argparse.ArgumentParser();p.add_argument('action',choices=['plan','job','assemble']);p.add_argument('--root',default='.')
    p.add_argument('--plan',required=True);p.add_argument('--phase',default='formal-cim-labels-v04');p.add_argument('--job-id');a=p.parse_args()
    root=Path(a.root).resolve();path=Path(a.plan).resolve()
    if a.action=='plan':
        document=json.loads(json.dumps(plan(root,a.phase)))
        if path.exists() and json.loads(path.read_text())!=document:raise ValueError('Frozen plan differs')
        write_json(path,document);print(json.dumps({'users':document['users'],'contexts':document['contexts'],'requests':document['total_requests']}))
    elif a.action=='job':run_job(root,path,a.job_id)
    else:assemble(root,path)

if __name__=='__main__':main()
