import json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from flashtrustfulrsi.sica_evolution import NativeSelector,record_benchmark,evolve

def rows(scores):
    return [{'problem_id':str(i),'score':s,'tokens_used':0,'num_cached_tokens':0,'cost_estimate':0,'wall_time':0} for i,s in enumerate(scores)]

class ArchiveInputTests(unittest.TestCase):
    def test_invalid_scores_do_not_enter_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            for scores in ([1],[1,None],[1,float('nan')],[1,-1]):
                with self.assertRaises(ValueError):record_benchmark(Path(tmp),rows(scores))
            self.assertFalse((Path(tmp)/'benchmarks').exists())

@unittest.skipUnless(os.environ.get('FTRSI_SICA_UPSTREAM'),'pinned external SICA source required')
class NativeArchiveTests(unittest.TestCase):
    def test_latest_tie_regression_and_actor_are_distinct(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for i,scores in enumerate(([1,1],[1,1],[0,0])):
                dest=root/f'agent_{i}';(dest/'agent_code').mkdir(parents=True);record_benchmark(dest,rows(scores))
            selector=NativeSelector(os.environ['FTRSI_SICA_UPSTREAM'])
            self.assertEqual(selector.select(root,2)[0],1)
            self.assertEqual(selector.module.ArchiveAnalyzer(root).get_best_agent_iteration(),0)
    def test_failure_never_promotes_partial_generation(self):
        with tempfile.TemporaryDirectory() as tmp,patch('flashtrustfulrsi.sica_evolution.run_meta',side_effect=RuntimeError('fixture transport failure')):
            with self.assertRaisesRegex(RuntimeError,'fixture transport'):
                evolve(source=os.environ['FTRSI_SICA_UPSTREAM'],output=tmp,image='fixture',provider_factory=lambda *a:None,evaluate=lambda *a:rows([0,0]),generations=1,protocol={'fixture':True})
            root=Path(tmp)
            self.assertTrue((root/'archive/agent_0/benchmarks/adapter/perf.json').exists())
            self.assertFalse((root/'archive/agent_1').exists())
            self.assertEqual(json.loads((root/'status.json').read_text())['status'],'failed')

if __name__=='__main__':unittest.main()
