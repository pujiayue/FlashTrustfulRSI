import io,json,sqlite3,subprocess,sys,tarfile,tempfile,unittest
from pathlib import Path
from flashtrustfulrsi.campaign_status import summarize,inventory
from flashtrustfulrsi.artifacts import read_artifact

class CampaignStatusTests(unittest.TestCase):
    def test_packaged_cli_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=subprocess.run([sys.executable,'-m','flashtrustfulrsi','status','--root',tmp],text=True,capture_output=True,check=True)
            self.assertEqual(json.loads(p.stdout),[])
    def test_archived_jobs_errors_and_stale_queue(self):
        with tempfile.TemporaryDirectory() as tmp:
            phase=Path(tmp)/'runs'/'formal-dojo-fixture';phase.mkdir(parents=True)
            db=sqlite3.connect(phase/'completed-index.sqlite3');db.execute('create table jobs(job text,status text)')
            db.executemany('insert into jobs values (?,?)',[('a',json.dumps({'status':'completed'})),('b',json.dumps({'status':'completed','execution_error':'iteration limit'}))]);db.commit();db.close()
            (phase/'a').mkdir();(phase/'a/status.json').write_text('{"status":"completed"}')
            (phase/'c').mkdir();(phase/'c/status.json').write_text('{"status":"failed"}')
            (phase/'queue-status.json').write_text('{"completed":1,"total":4,"status":"stopped_on_failure"}')
            got=summarize(phase)
            self.assertEqual(got['completed_jobs'],2);self.assertEqual(got['execution_error_jobs'],1)
            self.assertEqual(got['observed_jobs'],3);self.assertFalse(got['queue_completed_matches_evidence'])
            self.assertEqual(len(inventory(tmp)),1)
    def test_standalone_cim_archive_without_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            job=Path(tmp)/'cim';job.mkdir()
            with tarfile.open(job/'evidence.tar.gz','w:gz') as tar:
                info=tarfile.TarInfo('trace.json');info.size=2;tar.addfile(info,io.BytesIO(b'{}'))
            self.assertEqual(read_artifact(tmp,'cim','trace.json'),b'{}')
            with self.assertRaises(ValueError):read_artifact(tmp,'cim','../escape')

if __name__=='__main__':unittest.main()
