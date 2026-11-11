"""Preserve FlashRSI's published 400/200/1400 SearchQA subset as native JSONL.

Uses the same fixed EvoOS source and validation as FlashRSI prepare_searchqa.py;
only the on-disk container changes from Parquet to JSONL, supported natively.
"""
import argparse,hashlib,json,urllib.request
from pathlib import Path

REVISION='2e2d97feb4a3394fd577017024e6d9f3176e9fbc'
PREFIX='Answer the question using the supplied context. Return only the short answer.\n\nContext:\n'
SEPARATOR='\n\nQuestion:\n'


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);a=p.parse_args();a.output.mkdir(parents=True,exist_ok=True)
    rawdir=a.output/'source';rawdir.mkdir(exist_ok=True)
    def fetch(name):
        path=rawdir/name
        if not path.exists():
            url=f'https://huggingface.co/datasets/jinjiajie/EvoOS/resolve/{REVISION}/benchmarks/searchqa/{name}'
            with urllib.request.urlopen(url,timeout=180) as response:data=response.read(100*1024*1024+1)
            if len(data)>100*1024*1024:raise ValueError('Dataset file exceeds bound')
            path.write_bytes(data)
        return path.read_bytes()
    source=json.loads(fetch('manifest.json'));seen=set();splits={};out=a.output/'prepared';out.mkdir(exist_ok=True)
    for split,count in {'train':400,'dev':200,'test':1400}.items():
        data=fetch(split+'.jsonl');digest=hashlib.sha256(data).hexdigest()
        if digest!=source['metadata']['sha256.'+split]:raise ValueError('Published source checksum mismatch')
        rows=[]
        for line in data.splitlines():
            row=json.loads(line);task_id=row['id'];prompt=row['input'];answers=row['answers']
            if row['grader']!='qa' or row['metadata']!={'task_type':'searchqa'}:raise ValueError('Unexpected native metadata')
            if task_id in seen or not isinstance(task_id,str) or not task_id.strip():raise ValueError('Duplicate/malformed ID')
            if not isinstance(prompt,str) or not prompt.startswith(PREFIX):raise ValueError('Unexpected prompt')
            context,sep,question=prompt[len(PREFIX):].rpartition(SEPARATOR)
            if not sep or not question.strip() or not context.strip() or PREFIX+context+SEPARATOR+question!=prompt:raise ValueError('Prompt roundtrip failed')
            if not isinstance(answers,list) or not answers or any(not isinstance(x,str) or not x.strip() for x in answers):raise ValueError('Invalid answers')
            seen.add(task_id);rows.append({'id':task_id,'question':question,'context':context,'answers':answers})
        if len(rows)!=count:raise ValueError('Split denominator changed')
        path=out/(split+'.jsonl');converted=''.join(json.dumps(row,ensure_ascii=False)+'\n' for row in rows).encode()
        if path.exists() and path.read_bytes()!=converted:raise ValueError('Prepared dataset changed')
        path.write_bytes(converted);splits[split]={'count':count,'source_sha256':digest,'prepared_sha256':hashlib.sha256(converted).hexdigest(),'ids':[r['id'] for r in rows]}
    manifest={'source':'jinjiajie/EvoOS','revision':REVISION,'scope':'complete_2000_question_published_SkillOpt_subset_not_full_SearchQA','splits':splits,
        'format_adaptation':'JSONL instead of Parquet; all source ids, order, splits, answers and reconstructed prompts preserved','upstream_manifest':source}
    (out/'manifest.json').write_text(json.dumps(manifest,indent=2));print(json.dumps({'output':str(out),'counts':{k:v['count'] for k,v in splits.items()}}))


if __name__=='__main__':main()
