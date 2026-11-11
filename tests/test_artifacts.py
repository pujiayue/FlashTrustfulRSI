import fcntl,json,tempfile,unittest
from pathlib import Path
from flashtrustfulrsi.artifacts import archive_batch,archived_result,completed_jobs,read_artifact

class ArtifactStore(unittest.TestCase):
 def test_roundtrip_keeps_failure_and_skips_active_job(self):
  with tempfile.TemporaryDirectory() as tmp:
   phase=Path(tmp)/'formal-dojo-fixture';phase.mkdir()
   for name,status in [('a','completed'),('b','completed_with_execution_error'),('active','completed')]:
    job=phase/name;job.mkdir();(job/'result.json').write_text(json.dumps({'status':status,'job':name}));(job/'manifest.json').write_text('{}')
    (job/'trace').mkdir();(job/'trace/raw.json').write_text('full original evidence '+name)
   with (phase/'active/job.lock').open('a') as lock:
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    self.assertEqual(archive_batch(phase),2);self.assertTrue((phase/'active/result.json').exists())
   self.assertFalse((phase/'a').exists());self.assertEqual(completed_jobs(phase),{'a','b','active'})
   self.assertEqual(archived_result(phase,'b')['status'],'completed_with_execution_error')
   self.assertEqual(read_artifact(phase,'a','trace/raw.json'),b'full original evidence a')
   self.assertEqual(archive_batch(phase),1);self.assertEqual(archive_batch(phase),0)
   with self.assertRaises(ValueError):read_artifact(phase,'a','../secret')

if __name__=='__main__':unittest.main()
