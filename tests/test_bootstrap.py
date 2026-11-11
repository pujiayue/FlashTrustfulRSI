import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import zipfile
from flashtrustfulrsi.bootstrap import prepare

class NativeBootstrapTests(unittest.TestCase):
    def test_source_verified_atomic_and_existing_mismatch_not_overwritten(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'configs').mkdir();data=b'print("native")\n'
            lock={'destination':'upstream/native','repo':'fixture','commit':'fixed','license':'fixture',
                  'files':{'native.py':{'sha256':hashlib.sha256(data).hexdigest(),'bytes':len(data),'mode':'100644'}}}
            (root/'configs/flashrsi-source.lock.json').write_text(json.dumps(lock));archive=root/'source.zip'
            with zipfile.ZipFile(archive,'w') as z:z.writestr('native-fixed/native.py',data)
            self.assertEqual(prepare(root,archive)['status'],'verified')
            (root/'upstream/native/native.py').write_text('changed')
            with self.assertRaises(ValueError):prepare(root,archive)
            self.assertEqual((root/'upstream/native/native.py').read_text(),'changed')
    def test_archive_path_traversal_does_not_publish_partial_source(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'configs').mkdir()
            (root/'configs/flashrsi-source.lock.json').write_text(json.dumps({'destination':'upstream/native','files':{}}))
            archive=root/'source.zip'
            with zipfile.ZipFile(archive,'w') as z:z.writestr('native/../../escape','x')
            with self.assertRaises(ValueError):prepare(root,archive)
            self.assertFalse((root/'upstream/native').exists())
