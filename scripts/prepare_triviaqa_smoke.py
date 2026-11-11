"""Freeze the first two bounded-context Wikipedia validation rows."""
import argparse
import hashlib
import json
from pathlib import Path

from pyarrow.parquet import ParquetFile


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source',type=Path)
    parser.add_argument('source_sha256')
    parser.add_argument('output',type=Path)
    args=parser.parse_args()
    if digest(args.source)!=args.source_sha256:
        raise ValueError('Source Parquet checksum mismatch')
    chosen=[]
    for batch in ParquetFile(args.source).iter_batches(batch_size=16):
        for row in batch.to_pylist():
            pages=row['entity_pages']
            context=pages['wiki_context']
            if not context or not all(isinstance(text,str) and text.strip() for text in context):
                continue
            if sum(map(len,context))+sum(map(len,pages['title']))>20000:
                continue
            if not row['answer']['normalized_aliases'] or row['question_id'] in {r['question_id'] for r in chosen}:
                continue
            chosen.append(row)
            if len(chosen)==2:
                break
        if len(chosen)==2:
            break
    if len(chosen)!=2:
        raise ValueError('Need two valid rows')
    with args.output.open('x') as f:
        for row in chosen:
            f.write(json.dumps(row,ensure_ascii=False)+'\n')
    with args.output.with_suffix('.manifest.json').open('x') as f:
        json.dump({'repo':'mandarjoshi/trivia_qa','config':'rc.wikipedia','split':'validation',
                   'revision':'0f7faf33a3908546c6fd5b73a660e0f8ff173c2f',
                   'source_sha256':args.source_sha256,'subset_sha256':digest(args.output),
                   'selection':'first two distinct valid rows, Wikipedia context and titles at most 20000 chars',
                   'task_ids':[row['question_id'] for row in chosen]},f,indent=2)


if __name__=='__main__':
    main()
