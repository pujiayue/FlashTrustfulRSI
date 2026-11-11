import unittest
from flashtrustfulrsi.development import guard_owner_run,official_arguments,ENTRIES

class DevelopmentRoutes(unittest.TestCase):
    def test_owner_public_run_enforces_official_transport_without_touching_interns(self):
        self.assertEqual(guard_owner_run('evopath-memory',[]),['--deepseek-debug'])
        self.assertEqual(guard_owner_run('misevolution-online',['--deepseek-debug']),['--deepseek-debug'])
        self.assertEqual(guard_owner_run('she-dojo-seed',[]),[])
        for value in ('--spool','--provider=old.json','--base-url=https://other.example','--provider-mode=direct'):
            with self.assertRaises(ValueError):guard_owner_run('agentdojo-direct',[value])
        with self.assertRaises(ValueError):guard_owner_run('sica-meta-live',[])

    def test_official_entry_catalog_is_explicit(self):
        self.assertEqual(ENTRIES['misevolution-online'],('misevolution_online',True))
        self.assertEqual(ENTRIES['sica'],('sica_deepseek_twins',False))
        with self.assertRaises(ValueError):official_arguments(['--model=gpt-5.6-sol'])
