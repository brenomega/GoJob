"""Supervisor is an independent module, tested without importing broken runner internals."""
import tempfile,unittest,json
from pathlib import Path
from unittest.mock import patch
try:
    import stable_supervisor as s
except ModuleNotFoundError:
    from automation.supervisor import stable_supervisor as s
from gojob_runner.versioning import bump

class VersionTests(unittest.TestCase):
    def test_version_patch_only_bug(self):
        self.assertEqual(bump('3.0.0','bug'),'3.0.1')
        self.assertTrue(s.exact_version('3.0.0','3.0.1','bug'))
        with self.assertRaises(RuntimeError):s.exact_version('3.0.0','3.1.0','bug')
    def test_version_minor_procedural(self):
        self.assertEqual(bump('3.0.7','procedural'),'3.1.0')
        self.assertTrue(s.exact_version('3.0.7','3.1.0','procedural'))
    def test_version_major_feature(self):
        self.assertEqual(bump('3.1.8','feature'),'4.0.0')
        self.assertTrue(s.exact_version('3.1.8','4.0.0','feature'))
    def test_incident_redacts_secrets(self):
        text=s.redact('KeyError secret=abcdefghi0123456789 token=abcgh0123456789')
        self.assertNotIn('abcdefghi0123456789',text)
        self.assertIn('REDACTED',text)
    def test_incident_bundle_does_not_reset_repository(self):
        with tempfile.TemporaryDirectory() as tmp:
            r=Path(tmp)/'repo';r.mkdir();(r/'user_dirty.txt').write_text('KEEP')
            with patch.object(s,'run',side_effect=RuntimeError('git unavailable')):
                folder,obj=s.incident(Path(tmp)/'home',r,'KeyError: no state','runner_crash')
            self.assertTrue((folder/'incident.json').exists())
            self.assertEqual((r/'user_dirty.txt').read_text(),'KEEP')

if __name__=='__main__':unittest.main()
