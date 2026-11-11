"""Freeze disjoint public HotpotQA and MuSiQue development inputs without inference."""

import argparse
from hashlib import file_digest, sha256
import json
from pathlib import Path

import pyarrow.parquet as pq


HOT_REVISION = '1908d6afbbead072334abe2965f91bd2709910ab'
MUSIQUE_REVISION = '22873a405dd809893b22ada0b499299fb612d2df'
MUSIQUE_SHA256 = '15fa63794d18a94ce12411aca6e2327e65b6e83b0b1490efab3f1962e48abf3b'


def digest(path):
    with path.open('rb') as stream:
        return file_digest(stream, 'sha256').hexdigest()


def question_key(row):
    return sha256(''.join(row['question'].casefold().split()).encode()).hexdigest()


def choose(ids, role, count):
    return sorted(ids, key=lambda ident: (sha256(f't1-multihop-v1:{role}:{ident}'.encode()).digest(), ident))[:count]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-root', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    root = args.data_root.resolve()
    prepared = root/'prepared/hotpotqa'
    manifest_path = prepared/'manifest.json'
    hot = json.loads(manifest_path.read_text())
    if hot['source'] != {'repo':'hotpotqa/hotpot_qa', 'config':'fullwiki', 'revision':HOT_REVISION}:
        raise ValueError('Unexpected HotpotQA source')
    for split, count in {'train':81403, 'dev':9044, 'test':7405}.items():
        item = hot['splits'][split]
        path = prepared/f'{split}.parquet'
        if (digest(path) != item['sha256'] or item['count'] != count
                or pq.ParquetFile(path).metadata.num_rows != count):
            raise ValueError(f'Prepared HotpotQA {split} does not match its manifest')
        for name, expected in item['source_sha256'].items():
            if digest(root/'raw'/Path(name).name) != expected:
                raise ValueError(f'Original HotpotQA shard {name} changed')

    music_path = root/'raw/musique_ans_v1.0_dev.jsonl'
    if digest(music_path) != MUSIQUE_SHA256:
        raise ValueError('Original MuSiQue development file changed')
    with music_path.open() as stream:
        music_rows = [json.loads(line) for line in stream if line.strip()]
    music_by_id = {row['id']:row for row in music_rows}
    if len(music_rows) != len(music_by_id) or any(row['answerable'] is not True for row in music_rows):
        raise ValueError('Unexpected MuSiQue-Ans development pool')

    roles = {}
    for role, split, count in (('hotpot-train','train',3), ('hotpot-heldout','dev',2)):
        path = prepared/f'{split}.parquet'
        ids = choose(pq.read_table(path, columns=['id'])['id'].to_pylist(), role, count)
        by_id = {row['id']:row for row in pq.read_table(path, filters=[('id','in',ids)]).to_pylist()}
        roles[role] = [by_id[ident] for ident in ids]
    roles['musique-transfer'] = [music_by_id[ident] for ident in choose(music_by_id, 'musique-transfer', 2)]

    combined = [row for rows in roles.values() for row in rows]
    if (len({row['id'] for row in combined}) != len(combined)
            or len({question_key(row) for row in combined}) != len(combined)):
        raise ValueError('Development slices contain repeated IDs or normalized questions')
    args.output.mkdir(parents=True)
    files = {}
    for role, rows in roles.items():
        path = args.output/f'{role}.jsonl'
        path.write_text(''.join(json.dumps(row, ensure_ascii=False, sort_keys=True)+'\n' for row in rows), encoding='utf-8')
        files[role] = {'file':path.name, 'sha256':digest(path), 'ids':[row['id'] for row in rows],
                       'question_sha256':[question_key(row) for row in rows]}
    report = {'scope':'development_only_t1_multihop_inputs', 'model_requests':0,
              'selection':'smallest sha256(t1-multihop-v1:role:id); fixed before inference',
              'hotpotqa':{'manifest_sha256':digest(manifest_path), **hot},
              'musique':{'repo':'bdsaglam/musique', 'revision':MUSIQUE_REVISION, 'native_split':'dev',
                         'sha256':MUSIQUE_SHA256, 'count':len(music_rows)},
              'partitions':{'hotpot-train':'prepared train', 'hotpot-heldout':'prepared dev',
                            'musique-transfer':'public MuSiQue-Ans dev'},
              'formal_hotpot_test_used':False, 'files':files}
    (args.output/'manifest.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
