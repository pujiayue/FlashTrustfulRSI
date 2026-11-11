"""CIM's exact-membership aggregation, with explicit label-quality accounting.

The upstream counts an original statement absent from both lists as unlabeled,
including when the model paraphrased it. No fuzzy mapping, repair or resampling
is performed. v04b strict failures remain intact. This separately versioned
protocol can reuse complete 30-trial observations with frozen provenance.
"""
import argparse,fcntl,hashlib,json,time
from pathlib import Path
from . import cim_formal as native
from .provider import write_json
STRICT_MIX=native.mix_labels


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def aggregate(statements,labels):
    statements=set(statements);filtered=[];unknown=[]
    if len(labels)!=3 or any(len(t)!=10 for t in labels):raise ValueError('Incomplete 30-trial denominator')
    for pi,trials in enumerate(labels):
        persona=[]
        for ti,label in enumerate(trials):
            if set(label)!={'share','private'} or any(not isinstance(label[k],list) or any(not isinstance(s,str) for s in label[k]) for k in label):raise ValueError('Malformed persona label')
            extras=sorted((set(label['share'])|set(label['private']))-statements)
            if extras:unknown.append({'persona':pi,'trial':ti,'unmatched_statements':extras})
            persona.append({k:[s for s in label[k] if s in statements] for k in label})
        filtered.append(persona)
    return STRICT_MIX(statements,filtered),{'strict_wording_valid':not unknown,'trials':30,
        'trials_with_unmatched_statements':len(unknown),'unmatched':unknown,
        'policy':'upstream_exact_membership; original absent statements unlabeled; no fuzzy mapping or retry'}


def run_job(root,plan_path,job_id):
    frozen=json.loads(plan_path.read_text())
    if frozen['native_label_wrapper_sha256']!=sha(__file__):raise ValueError('Wrapper changed')
    case=next(j for j in frozen['jobs'] if j['job_id']==job_id)
    reuse=frozen.get('reuse',{}).get(job_id)
    dest=root/'runs'/frozen['phase_id']/job_id;dest.mkdir(parents=True,exist_ok=True)
    if reuse:
        with (dest/'job.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            if (dest/'result.json').exists():return
            up=next((root/'upstream').glob('facebookresearch-CIMemories-*'))
            for base,files in ((up,frozen['source_files']),(Path(native.__file__).parent,frozen['adapter_files'])):
                for name,digest in files.items():
                    if sha(base/name)!=digest:raise ValueError('Frozen source changed: '+name)
            source=root/reuse['snapshot']
            for name,digest in reuse['files'].items():
                if sha(source/name)!=digest:raise ValueError('Reused observation changed')
            profile=json.loads((up/'data/data_openai_gpt-oss-120b.json').read_text())[case['user_index']]
            manifest=json.loads((source/'manifest.json').read_text())
            if any(manifest[k]!=v for k,v in case.items()):raise ValueError('Reused context mismatch')
            labels=json.loads((source/'parsed-labels.json').read_text())
            mixed,quality=aggregate([v['memory_statement'] for v in profile['information_attributes'].values()],labels)
            write_json(dest/'manifest.json',{'plan_sha256':sha(plan_path),**case,'reuse':reuse})
            write_json(dest/'frozen-labels.json',mixed);write_json(dest/'label-quality.json',quality)
            write_json(dest/'result.json',{'status':'completed','scope':frozen['scope'],**case,'trials':30,
                'labels_sha256':sha(dest/'frozen-labels.json'),'labels_source':frozen['labels'],'human_review':'pending',
                'label_quality':quality,'reused_observation':reuse,'new_requests':0,
                'usage_semantics':'original source-phase usage retained; reused observations are not independent trials'})
            write_json(dest/'status.json',{'status':'completed','finished':time.time()})
        return
    from .durable_transport import DurableProvider
    native.RecordedNativeProvider=DurableProvider
    original_mix=native.mix_labels
    def mix(statements,labels):
        mixed,quality=aggregate(statements,labels)
        write_json(dest/'label-quality.json',quality)
        return mixed
    native.mix_labels=mix
    try:
        native.run_job(root,plan_path,job_id)
        result=json.loads((dest/'result.json').read_text());result['label_quality']=json.loads((dest/'label-quality.json').read_text())
        write_json(dest/'result.json',result)
    finally:native.mix_labels=original_mix


def main():
    p=argparse.ArgumentParser();p.add_argument('action',choices=['job']);p.add_argument('--root',type=Path,required=True)
    p.add_argument('--plan',type=Path,required=True);p.add_argument('--job-id',required=True);a=p.parse_args()
    run_job(a.root.resolve(),a.plan.resolve(),a.job_id)

if __name__=='__main__':main()
