"""Validate public SEABench sequences and inventory its separate monitor split."""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import sys


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('release', type=Path)
    args = parser.parse_args()
    release = args.release.resolve()
    sys.path.insert(0, str(release))
    from runtime.task_loader import load_yaml_task_suite

    tasks = load_yaml_task_suite(release / 'tasks')
    by_path = defaultdict(list)
    for task in tasks:
        by_path[task.source_task_file].append(task)
    files = sorted((release / 'tasks').glob('**/*.yaml'))
    if len(files) != 48 or len(by_path) != len(files):
        raise ValueError('Expected 48 active sequence files from the pinned upstream release')
    sequences = []
    for path in files:
        relative = path.relative_to(release)
        rows = by_path[str(relative)]
        if len({row.task_id for row in rows}) != len(rows):
            raise ValueError(f'Duplicate task ID in {relative}')
        roles = Counter(row.optimization_role for row in rows)
        if not roles['upstream'] or not roles['downstream']:
            raise ValueError(f'Missing upstream or downstream task in {relative}')
        sequences.append({'path': str(relative), 'sha256': sha(path),
                          'sequence_id': rows[0].sequence_id,
                          'roles': dict(roles),
                          'validators': dict(Counter(row.validation_backend for row in rows)),
                          'tasks': [{'id': row.task_id, 'role': row.optimization_role,
                                     'validator': row.validation_backend} for row in rows]})
    checkpoint = release / 'prompt_optimization_analysis/reasoning_monitor/checkpoint'
    split = checkpoint / 'split.json'
    split_ids = json.loads(split.read_text())['trace_ids']
    id_counts = Counter(row['sequence_id'] for row in sequences)
    print(json.dumps({
        'status': 'completed', 'scope': 'native loader and release manifest integrity; no behavioral evaluation',
        'public_sequences': len(sequences), 'public_tasks': len(tasks),
        'roles': dict(Counter(row.optimization_role for row in tasks)), 'sequences': sequences,
        'duplicate_sequence_ids': {key: n for key, n in id_counts.items() if n > 1},
        'identity_key': 'relative sequence path plus task_id; native sequence IDs can recur',
        'monitor_split_sha256': sha(split), 'monitor_split_counts': {k: len(v) for k, v in split_ids.items()},
        'annotated_corpus_status': 'separate graph/thread corpus not located in pinned public release',
        'model_requests': 0,
    }, indent=2))


if __name__ == '__main__':
    main()
