"""External CIMemories native prompts, persona labels, and metric execution.

CC BY-NC upstream is loaded from an explicitly supplied research dependency.
No fabricated labels, permissive parse fallback, or independent-judge claim.
"""
import argparse, ast, copy, hashlib, json, re, subprocess, sys, typing
from collections import Counter
from pathlib import Path
from textwrap import dedent
from types import SimpleNamespace
import yaml
from .provider import write_json
from .transport import RecordedProvider, MODEL

def load_functions(path,names,namespace,assignments=()):
    nodes=[]
    for n in ast.parse(path.read_text()).body:
        if isinstance(n,ast.FunctionDef) and n.name in names:nodes.append(n)
        elif isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id in assignments for t in n.targets):nodes.append(n)
    exec(compile(ast.Module(body=nodes,type_ignores=[]),str(path),'exec'),namespace)
    return namespace

def strict_json(text):
    match=re.search(r'\{[\s\S]*\}',text)
    if not match:raise ValueError('Missing JSON response')
    result=json.loads(match.group(0))
    if not isinstance(result,dict):raise ValueError('Expected JSON object')
    return result

def main():
    a=argparse.ArgumentParser();a.add_argument('--root',default='.');a.add_argument('--job-id',required=True)
    a.add_argument('--contexts',type=int,default=2);a.add_argument('--plan-only',action='store_true');args=a.parse_args()
    root=Path(args.root).resolve();up=next((root/'upstream').glob('facebookresearch-CIMemories-*'))
    datafile=up/'data/data_openai_gpt-oss-120b.json';profile=copy.deepcopy(json.loads(datafile.read_text())[0]);profile['contexts']=profile['contexts'][:args.contexts]
    spec={'method':'CIMemories','profile':'full_memory_persona_labels_canary','model':MODEL,'job_id':args.job_id,
          'upstream_commit':'e39806e92ec66db120c0f3b73c9cb105e7ff5314','user_index':0,'context_indices':list(range(args.contexts)),
          'attributes':len(profile['information_attributes']),'personas':3,'label_trials_per_persona':10,
          'persona_weights':[0.26,0.64,0.10],'target_trials':1,'privacy_prompt_levels':[1,2],'max_requests':args.contexts*34,
          'max_output_tokens':8192,'judge_independent':False,'human_review':'pending','label_source':'new_model_generated',
          'source_hashes':{str(p.relative_to(up)):hashlib.sha256(p.read_bytes()).hexdigest() for p in [datafile,up/'data/persona_gold_labels.py',up/'eval/eval.py',up/'eval/prompts.yaml',up/'eval/metrics.py']},
          'adapter_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
          'modifications':['first user, first two contexts; all attributes preserved','gpt-5.6-sol for labeling, target, disclosure judge',
                           'strict parse failures instead of zero disclosure','no retries; recorded transport','static prompt comparison; not an RSI update experiment']}
    if args.plan_only:print(json.dumps(spec,indent=2));return
    dest=root/'runs'/args.job_id;dest.mkdir(parents=True,exist_ok=True)
    if (dest/'manifest.json').exists() and json.loads((dest/'manifest.json').read_text())!=spec:raise ValueError('Frozen manifest mismatch')
    write_json(dest/'manifest.json',spec)
    if (dest/'result.json').exists():print((dest/'result.json').read_text());return
    p=RecordedProvider(json.loads((root/'configs/provider-v02.json').read_text()),root/'runs/v02-canary',args.job_id)
    def complete(prompt,identity):
        r=p.invoke('chat',{'model':MODEL,'messages':[{'role':'user','content':prompt}],'max_tokens':8192},request_id=identity)
        print(json.dumps({'event':'completion','call_id':identity,'usage':r.get('usage',{})}),flush=True)
        return r['choices'][0]['message']['content']
    ns=load_functions(up/'data/persona_gold_labels.py',{'label_attributes'},
                      {'dedent':dedent,'completion_cost':lambda r:0},assignments=('personas',))
    ev=load_functions(up/'eval/eval.py',{'memory_map','attr_lines','mem_blob'},dict(vars(typing)))
    prompts=yaml.safe_load((up/'eval/prompts.yaml').read_text());raw_labels=[]
    statements={v['memory_statement'] for v in profile['information_attributes'].values()}
    write_json(dest/'status.json',{'status':'running','phase':'labels'})
    try:
        for ci,context in enumerate(profile['contexts']):
            distributions=[]
            for pi,persona in enumerate(ns['personas']):
                class LabelModel:
                    def raw(self,prompt,n=1):
                        return [SimpleNamespace(choices=[SimpleNamespace(message={'content':complete(prompt,f'label-c{ci}-p{pi}-t{t}')})]) for t in range(n)]
                texts,_=ns['label_attributes'](LabelModel(),persona,profile,context,n=10)
                counts={s:Counter(necessary=0,private=0,unlabeled=0) for s in statements}
                for ti,text in enumerate(texts):
                    label=strict_json(text)
                    if set(label)!={'share','private'} or any(not isinstance(label[k],list) or any(not isinstance(v,str) for v in label[k]) for k in label):raise ValueError('Malformed persona labels')
                    share=set(label['share']);private=set(label['private'])
                    if (share|private)-statements:raise ValueError('Persona changed memory wording')
                    for s in statements:counts[s]['necessary' if s in share else 'private' if s in private else 'unlabeled']+=1
                    raw_labels.append({'context':ci,'persona':pi,'trial':ti,'label':label})
                distributions.append({s:{k:v/10 for k,v in c.items()} for s,c in counts.items()})
                write_json(dest/'raw_labels.json',raw_labels)
            context['labels_combined']={s:{k:sum(w*d[s][k] for w,d in zip((.26,.64,.10),distributions)) for k in ('private','necessary','unlabeled')} for s in statements}
        write_json(dest/'frozen_labels.json',[profile]);label_hash=hashlib.sha256((dest/'frozen_labels.json').read_bytes()).hexdigest()
        results=[];names=list(profile['information_attributes']);mem=ev['memory_map'](profile)
        for level in (1,2):
            evaluated=copy.deepcopy(profile)
            for ci,ctx in enumerate(evaluated['contexts']):
                prompt=prompts['task_solving'][level].format(memories=ev['mem_blob'](names,mem),recipient=ctx['recipient'].lower(),task=ctx['task'])
                solution=complete(prompt,f'task-level{level}-c{ci}')
                judge=prompts['adversary_reveal'][1].format(attributes='\n'.join(ev['attr_lines'](names,mem)),solution=solution)
                disclosed=strict_json(complete(judge,f'disclosure-level{level}-c{ci}'))
                if set(disclosed)-set(names):raise ValueError('Unknown disclosed attribute')
                if any(not isinstance(v,str) for v in disclosed.values()):raise ValueError('Missing disclosure evidence')
                ctx[f'combination_num_to_share={len(names)}_num_not_to_share=0_trial=0']={
                    'prompt':prompt,'to_share_attributes':names,'not_to_share_attributes':[],'num_to_share_attributes':len(names),
                    'num_not_to_share_attributes':0,'total_num_attributes':len(names),'trial_idx':0,
                    'model_solution':{'response_solution':solution,'revealed_attributes':{n:int(n in disclosed) for n in names},'revealed_attributes_explanation':disclosed}}
            path=dest/f'level-{level}.jsonl';path.write_text(json.dumps(evaluated,ensure_ascii=False)+'\n')
            scored=subprocess.run([sys.executable,str(up/'eval/metrics.py'),'--path',str(path)],capture_output=True,text=True,check=True)
            (dest/f'level-{level}-native-metrics.txt').write_text(scored.stdout+'\n'+scored.stderr)
            results.append({'level':level,'native_metrics':scored.stdout,'disclosure_calls':args.contexts,'target_calls':args.contexts})
        write_json(dest/'result.json',{'status':'completed','scope':'live_CIM_canary_not_full_paper','model':MODEL,'frozen_labels_sha256':label_hash,
                                     'users':1,'contexts':args.contexts,'label_calls':args.contexts*30,'label_trials':len(raw_labels),
                                     'results':results,'judge_independent':False,'human_review':'pending'})
        write_json(dest/'status.json',{'status':'completed'});print(json.dumps({'status':'completed','output':str(dest)}),flush=True)
    except Exception as e:
        write_json(dest/'status.json',{'status':'failed','error_type':type(e).__name__,'error':str(e)});raise
if __name__=='__main__':main()
