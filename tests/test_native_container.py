import base64,tempfile,unittest
from pathlib import Path
from flashtrustfulrsi.native_container import apply_export


class NativeExportTests(unittest.TestCase):
    def test_rejects_traversal_before_mutation(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'original').write_text('keep')
            with self.assertRaises(ValueError):apply_export(root,{'../outside':{'data':'YQ==','mode':0o644}})
            self.assertEqual((root/'original').read_text(),'keep')

    def test_rejects_symlink_checkout(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'link').symlink_to('/tmp')
            with self.assertRaises(ValueError):apply_export(root,{'link/write':{'data':'YQ==','mode':0o644}})

    def test_roundtrip_preserves_bytes_and_executable_bit(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'obsolete').write_text('old')
            apply_export(root,{'tools/run.sh':{'data':base64.b64encode(b'#!/bin/sh\ntrue\n').decode(),'mode':0o755}})
            self.assertFalse((root/'obsolete').exists())
            self.assertEqual((root/'tools/run.sh').read_bytes(),b'#!/bin/sh\ntrue\n')
            self.assertEqual((root/'tools/run.sh').stat().st_mode&0o777,0o755)

    def test_rejects_privilege_bits(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError):apply_export(Path(d),{'a':{'data':'YQ==','mode':0o4755}})

    def test_full_export_preserves_readonly_files_and_rejects_changes_before_mutation(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);protected=root/'agent.yaml';other=root/'obsolete'
            protected.write_bytes(b'a');protected.chmod(0o444);other.write_bytes(b'old')
            for payload in ({},{'agent.yaml':{'data':'Yg==','mode':0o644}}):
                with self.assertRaisesRegex(ValueError,'read-only checkout file'):
                    apply_export(root,payload)
                self.assertEqual(other.read_bytes(),b'old')
            apply_export(root,{'agent.yaml':{'data':'YQ==','mode':0o644},'skills/new':{'data':'Yg==','mode':0o644}})
            self.assertEqual(protected.stat().st_mode&0o777,0o444)
            self.assertEqual((root/'skills/new').read_bytes(),b'b')


if __name__=='__main__':unittest.main()
