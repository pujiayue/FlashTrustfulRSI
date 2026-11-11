import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from flashtrustfulrsi.seabench_monitor import inspect_corpus_files, summarize_shards


class MonitorShardTests(unittest.TestCase):
    ids = ['controller/a', 'controller/b', 'memory/c', 'tools_skills/d', 'tools_skills/e']
    lock = {'split.json': 'frozen-split'}

    def test_inventory_requires_both_exact_paths_inside_corpus(self):
        with TemporaryDirectory() as temporary:
            staging = Path(temporary)
            root = staging / 'corpus'
            graph = root / 'controller_update/reasoning_graphs/run/task.01.json'
            label = root / 'controller_update/risk_thread_safety_labels_v2/run/task.01.json'
            for path in (graph,label):
                path.parent.mkdir(parents=True)
            graph.write_text('{}')
            external = staging / 'external.json'
            external.write_text('{}')
            label.symlink_to(external)
            split = {'test':['controller/run/task.01']}
            incomplete = inspect_corpus_files(root,split)
            self.assertFalse(incomplete['complete_file_pairs'])
            self.assertEqual(incomplete['missing'][0]['missing_or_external_paths'],[label.relative_to(root).as_posix()])
            label.unlink()
            label.write_text('{}')
            complete = inspect_corpus_files(root,split)
        self.assertTrue(complete['complete_file_pairs'])
        self.assertFalse(complete['native_validation_executed'])
        self.assertEqual(complete['partitions']['test']['controller']['complete_pairs'],1)

    def shard(self, root, name, offset, rows, **overrides):
        path = root / name
        path.mkdir()
        cases = [{'trace_id': trace, 'surface': trace.split('/')[0], 'outcome': outcome,
                  'prediction': prediction, 'response_cot_available': cot,
                  'prediction_status': 'invalid_verifier' if prediction is None else 'scored'}
                 for trace, outcome, prediction, cot in rows]
        scope = 'released_monitor_test_subset'
        manifest = {'job_id': name, 'scope': scope, 'partition': 'test', 'checkpoint_sha256': self.lock,
                    'corpus_sha256': 'frozen-corpus', 'model': 'model', 'source_sha256': {},
                    'adapter_sha256': 'adapter', 'verifier_protocol': 'chat', 'offset': offset,
                    'cases': [row['trace_id'] for row in cases], **overrides}
        documents = {'manifest': manifest, 'status': {'status': 'completed'},
                     'result': {'scope': scope, 'status': 'completed', 'model': manifest['model'], 'cases': cases}}
        for key, value in documents.items():
            (path / f'{key}.json').write_text(json.dumps(value))
        return path

    def test_exclusions_and_coverage(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = self.shard(root, 'first', 0, [(self.ids[0], 'failure', True, True),
                                                  (self.ids[1], 'safe', True, True)])
            last = self.shard(root, 'last', 2, [(self.ids[2], 'safe', None, True),
                                                (self.ids[3], 'failure', False, False)])
            output = summarize_shards([last, first], self.ids, self.lock)
        self.assertEqual(output['missing_trace_ids'], [self.ids[4]])
        self.assertFalse(output['complete_test_coverage'])
        counts = output['surfaces']
        self.assertEqual(counts['controller']['failure_recall'], 1)
        self.assertEqual(counts['controller']['fpr'], 1)
        self.assertEqual(counts['memory']['invalid_verifier'], 1)
        self.assertIsNone(counts['memory']['fpr'])
        self.assertEqual(counts['tools_skills']['missing_response_cot'], 1)
        self.assertIsNone(counts['tools_skills']['failure_recall'])

    def test_overlap_and_provenance_are_rejected(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            case = [(self.ids[0], 'safe', False, True)]
            first = self.shard(root, 'first', 0, case)
            overlap = self.shard(root, 'overlap', 0, case)
            changed = self.shard(root, 'changed', 1, [(self.ids[1], 'safe', False, True)], model='other')
            with self.assertRaisesRegex(ValueError, 'overlapping'):
                summarize_shards([first, overlap], self.ids, self.lock)
            with self.assertRaisesRegex(ValueError, 'share a corpus'):
                summarize_shards([first, changed], self.ids, self.lock)
