"""Audit local developer requests against sealed test IDs/questions; no API calls."""
from collections import defaultdict
import argparse
from hashlib import sha256
import json
from pathlib import Path
import re
import shutil
import tempfile
import sqlite3
from .provider import write_json


def strings(value):
    if isinstance(value,str):yield value
    elif isinstance(value,dict):
        for item in value.values():yield from strings(item)
    elif isinstance(value,list):
        for item in value:yield from strings(item)


def audit(root,prepared):
    root=Path(root).resolve();prepared=Path(prepared).resolve()
    records=[json.loads(line) for line in (prepared/'test.jsonl').read_text().splitlines() if line.strip()]
    anchors=defaultdict(list);by_id={str(row['id']):row for row in records}
    for row in records:
        q=row['question'];words=re.findall(r'\w+',q)
        anchor=max(words,key=len) if words else q
        if not anchor:raise ValueError('Empty test question')
        anchors[anchor].append((q,str(row['id'])))
        identifier=str(row['id']);id_words=re.findall(r'\w+',identifier)
        anchors[max(id_words,key=len) if id_words else identifier].append((identifier,identifier))
    token_pattern=re.compile(r'\w+')
    exposed=defaultdict(list);examined=0;ledger_count=0
    def inspect(body,origin,identity,state):
        nonlocal examined
        examined+=1;text='\n'.join(strings(body)).replace('\\"','"')
        candidates=set()
        for anchor in set(token_pattern.findall(text)) & anchors.keys():
            for needle,key in anchors[anchor]:
                if needle in text:candidates.add(key)
        for key in candidates:exposed[key].append({'origin':origin,'call_id':identity,'state':state})
    for db in sorted((root/'runs').rglob('deepseek-debug.sqlite3')):
        if not any(part.startswith('dev-dsflash-') for part in db.relative_to(root/'runs').parts):continue
        ledger_count+=1
        with sqlite3.connect(db.as_uri()+'?mode=ro',uri=True) as connection:
            for identity,request,state in connection.execute('SELECT call_id,request,status FROM calls'):
                inspect(json.loads(request),str(db.relative_to(root)),identity,state)
    spool=root/'runs/model-spool'
    if spool.exists():
        for directory in sorted(spool.iterdir()):
            if not directory.is_dir() or not directory.name.startswith(('owner-live-','dev-dsflash-')):continue
            for path in directory.glob('*.request.json'):
                inspect(json.loads(path.read_text()),str(path.relative_to(root)),path.stem,'spooled_possible_exposure')
    entries=[]
    for key,requests in sorted(exposed.items()):
        entries.append({'id':key,'question_sha256':sha256(by_id[key]['question'].encode()).hexdigest(),'observations':requests})
    return {'schema':'flashtrustfulrsi.development-exposure.v1','model_requests':0,
            'scope':'current owner root official development ledgers and owner-live development spool only; not other hosts or formal-job archives',
            'prepared':str(prepared),'source_sha256':{split:sha256((prepared/(split+'.jsonl')).read_bytes()).hexdigest() for split in ('train','dev','test')},
            'test_rows':len(records),'developer_requests_examined':examined,'official_ledgers_examined':ledger_count,
            'exposed_test_rows':len(entries),'remaining_unmatched_rows':len(records)-len(entries),'exposures':entries,
            'policy':'Exact original question or task ID matches, including pending/failed/spooled requests, are conservatively marked exposed. No paraphrase-detection or global-unseen guarantee.',
            'formal_jobs_changed':False}


def clean_copy(report,destination):
    source=Path(report['prepared']);destination=Path(destination).resolve()
    if destination.exists():raise FileExistsError('Choose a new clean data directory; original data are never overwritten')
    for split,digest in report['source_sha256'].items():
        if sha256((source/(split+'.jsonl')).read_bytes()).hexdigest()!=digest:raise ValueError('Prepared data changed after exposure audit')
    destination.parent.mkdir(parents=True,exist_ok=True)
    excluded={r['id'] for r in report['exposures']};splits={}
    with tempfile.TemporaryDirectory(prefix='ftrsi-clean-data-',dir=destination.parent) as tmp:
        stage=Path(tmp)/'prepared';stage.mkdir()
        for split in ('train','dev','test'):
            data=(source/(split+'.jsonl')).read_bytes()
            if split=='test':data=b''.join(line for line in data.splitlines(keepends=True) if str(json.loads(line)['id']) not in excluded)
            (stage/(split+'.jsonl')).write_bytes(data)
            splits[split]={'rows':len(data.splitlines()),'prepared_sha256':sha256(data).hexdigest()}
        manifest={'schema':'flashtrustfulrsi.searchqa.unexposed-sensitivity.v1','source_sha256':report['source_sha256'],
                  'splits':splits,'excluded_test_ids':sorted(excluded),'original_data_modified':False,
                  'claim':'No exact match in the audited local developer request scope; not proof of globally unseen questions.',
                  'launch_enabled':False,'historical_formal_plan_modified':False}
        write_json(stage/'manifest.json',manifest);stage.rename(destination)
    return manifest



def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',default='.')
    p.add_argument('--prepared',type=Path);p.add_argument('--output',required=True,type=Path)
    p.add_argument('--clean-output',type=Path);args=p.parse_args(argv)
    data=args.prepared or Path(args.root)/'datasets/searchqa-flashrsi-v04/prepared'
    report=audit(args.root,data)
    if args.clean_output:report['clean_copy']=clean_copy(report,args.clean_output)
    write_json(args.output,report)
    print(json.dumps({k:report[k] for k in ('test_rows','exposed_test_rows','remaining_unmatched_rows','developer_requests_examined','model_requests')},indent=2))
if __name__=='__main__':main()
