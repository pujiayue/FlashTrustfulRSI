"""Freeze two disjoint MMLU/SALAD pairs from verified public source files."""
import argparse
import hashlib
import json
from pathlib import Path

import pyarrow.parquet as pq


FILES = {
    'mmlu-validation.parquet': 'b66df87644982b96b6708b1748cf5cdd4c45c476b80ee5f33d1bf104d397f840',
    'mmlu-test.parquet': '92338cdcdb43d2871691ef9a0e125e633ca57335ed635a635fe6dead94f224c2',
    'salad.json': '389da12f029a7f0c4a562f753e1dd5e53aed46d873ef1f568919d9f4b72e7062',
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    for name, expected in FILES.items():
        if hashlib.sha256((args.source/name).read_bytes()).hexdigest() != expected:
            raise ValueError('Source changed: '+name)
    result = {'provenance': {
        'cais/mmlu': 'c30699e8356da336a370243923dbaf21066bb9fe',
        'OpenSafetyLab/Salad-Data': 'd21a325e276a99bd69b1fbb8aa51a9f249486b72',
        'files': FILES, 'selection': 'MMLU abstract_algebra first row of each split; SALAD first two distinct qids',
    }}
    salad = json.loads((args.source/'salad.json').read_text())
    for split, safety in [('validation',salad[0]),('test',next(row for row in salad if row['qid']!=salad[0]['qid']))]:
        row = pq.read_table(args.source/f'mmlu-{split}.parquet').slice(0,1).to_pylist()[0]
        labels = 'ABCD'
        prompt = row['question']+'\n'+'\n'.join(f'{k}: {choice}' for k,choice in zip(labels,row['choices'],strict=True))
        result[split] = {
            'mmlu': {'id': f'abstract_algebra/{split}/0','prompt': prompt,'answer':labels[row['answer']],
                     'format': 'One capital letter A, B, C or D.'},
            'salad': {'id': str(safety['qid']), 'prompt':safety['augq'], 'baseq':safety['baseq'],
                      'format':'A short text response.'},
        }
    with args.output.open('x') as out:
        json.dump(result,out,indent=2,ensure_ascii=False)


if __name__ == '__main__':
    main()
