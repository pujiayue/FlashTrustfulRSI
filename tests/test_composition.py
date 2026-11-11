"""Trust pairing rejects invalid evidence; native policies remain authoritative."""
import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest

from flashtrustfulrsi.composition import AuditSpec, TrustSession, ComponentAuditEvaluator, native_inventory, paired_metric


class PairTests(unittest.TestCase):
    def test_a_selector_cannot_be_mislabelled_as_a_metric(self):
        with self.assertRaises(ValueError):
            ComponentAuditEvaluator('agentbreeder-pareto',root='.',evidence_mapper=lambda context:{},dimensions=('D6',))
    def row(self, key, score=1, status='finished'):
        return NS(task=NS(task_id=key), status=status, metrics={'em': score})

    def test_pair_alignment_and_regression(self):
        out = paired_metric([self.row('a',1),self.row('b',0)],
                            [self.row('b',1),self.row('a',0)],'em')
        self.assertEqual((out['delta'],out['regression_rate']),(0,.5))
        with self.assertRaises(ValueError):
            paired_metric([self.row('a')],[self.row('b')],'em')
        with self.assertRaises(ValueError):
            paired_metric([self.row('a'),self.row('a')],[self.row('a')],'em')

    def test_failed_or_missing_scores_are_not_safe_zero(self):
        self.assertIsNone(paired_metric([self.row('a')],[self.row('a',status='error')],'em')['value'])
        with self.assertRaises(ValueError):
            paired_metric([self.row('a')],[self.row('a')],'absent-safety-label')


@unittest.skipUnless(importlib.util.find_spec('flashrsi'),'optional pinned native package')
class NativeCompositionTests(unittest.IsolatedAsyncioTestCase):
    async def test_observer_restores_policy_and_defers_holdout(self):
        from flashrsi.core import Task, Trajectory
        native_seen=[]
        class Snapshot:
            def __init__(self, key): self.id=key
            def save(self,path): Path(path).write_text(json.dumps({'id':self.id}))
        class Algo:
            target=NS(snapshot=Snapshot('parent'))
            selected=False
            def _record(self,kind,payload): native_seen.append((kind,payload))
            async def evolve(self):
                self._record('decision',{'accepted':False,'parent_id':'parent','candidate_id':'parent'})
                self.selected=True
                return self.target.snapshot
        algorithm=Algo()
        original=algorithm._record
        task=Task('heldout','hidden question')
        class Target:
            snapshot=Snapshot('parent')
            component_rules={'prompt': ['prompt.md']}
            def update(self,snapshot): self.snapshot=snapshot
            def set_scope(self,scope): self.scope=scope
            async def rollout(self,tasks,*,snapshot):
                self_test.assertTrue(algorithm.selected)
                return (Trajectory(task=task,metrics={'exact_match':1.}),)
        class Bench:
            primary_metric='exact_match'
            def load(self): return {'train':(), 'dev':(), 'test':(task,)}
            def grade(self,rows): return rows
        def benchmark_factory(config):
            self.assertTrue(algorithm.selected)
            return Bench()
        self_test=self
        with tempfile.TemporaryDirectory() as temporary:
            result=await TrustSession(Path(temporary)/'audit').evolve(algorithm,[AuditSpec('heldout',{})],
                      lambda role:Target(),benchmark_factory)
            self.assertEqual(result['audits']['heldout']['native-paired-utility']['exact_match']['delta'],0)
            self.assertEqual(algorithm._record,original)
            self.assertEqual(native_seen[0][1]['accepted'],False)

    async def test_cached_audit_uses_original_observations_without_constructing_target(self):
        from flashrsi.core import Task,Trajectory
        class Snapshot:
            id='original'
            def save(self,path):Path(path).write_text('{"id":"original"}')
        task=Task('case','frozen question')
        row=Trajectory(task=task,output='recorded answer',metrics={'em':1.})
        class Bench:
            primary_metric='em'
            def load(self):return {'train':(),'dev':(),'test':(task,)}
            def grade(self,rows):return rows
        def forbidden(role):raise AssertionError('Cached roles must not construct or invoke a target')
        with tempfile.TemporaryDirectory() as tmp:
            session=TrustSession(Path(tmp)/'audit')
            result=await session.audit(Snapshot(),Snapshot(),[AuditSpec('heldout',{})],forbidden,lambda config:Bench(),
                cached_pairs={('heldout','initial'):(row,),('heldout','selected'):(row,)})
            self.assertEqual(result['audits']['heldout']['native-paired-utility']['em']['delta'],0.)
            wrong=Trajectory(task=Task('other','changed question'),output='answer',metrics={'em':1.})
            session=TrustSession(Path(tmp)/'wrong')
            with self.assertRaises(ValueError):
                await session.audit(Snapshot(),Snapshot(),[AuditSpec('heldout',{})],forbidden,lambda config:Bench(),
                  cached_pairs={('heldout','initial'):(wrong,)})

    async def test_registry_delegates_to_native_objects(self):
        from flashrsi import runner
        inventory=native_inventory()
        self.assertEqual(set(inventory['benchmarks']),set(runner._BENCHMARKS))
        self.assertEqual(set(inventory['algorithms']),set(runner._ALGORITHMS))
        self.assertEqual(set(inventory['harnesses']),set(runner._AGENTS))


if __name__=='__main__': unittest.main()
