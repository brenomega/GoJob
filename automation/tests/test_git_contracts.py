"""Real Git-based tests: object identity and immutable base, no mocks of Git itself."""
import subprocess,tempfile,unittest
from pathlib import Path
from gojob_runner.tools import commit_audit,PolicyFailure,assert_clean,assert_base_ancestor

def git(path,*args):
    return subprocess.check_output(['git',*args],cwd=path,text=True,stderr=subprocess.DEVNULL).strip()

class RealGit(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.repo=Path(self.tmp.name)
        git(self.repo,'init','-q');git(self.repo,'config','user.name','Test Dev');git(self.repo,'config','user.email','dev@example.test')
        (self.repo/'file.py').write_text('VALUE = 1\n')
        git(self.repo,'add','.');git(self.repo,'commit','-q','-m','base')
        self.base=git(self.repo,'rev-parse','HEAD')
    def tearDown(self):self.tmp.cleanup()
    def commit(self,message,contents='VALUE = 2\n'):
        (self.repo/'file.py').write_text(contents)
        git(self.repo,'add','.');git(self.repo,'commit','-q','-m',message)
    def test_logical_commit_and_immutable_base(self):
        self.commit('fix(radar): impeça repetição de candidatura [#1.2.1]')
        report=commit_audit(self.repo,self.base,'1.2.1')
        self.assertEqual(len(report['commits']),1)
        self.assertNotEqual(report['head'],self.base)
    def test_reject_nonconforming_commit(self):
        self.commit('bad')
        with self.assertRaisesRegex(PolicyFailure,'Assunto inválido'):commit_audit(self.repo,self.base,'1.2.1')
    def test_reject_wrong_issue_id(self):
        self.commit('fix(radar): impeça repetição de candidatura [#9.9]')
        with self.assertRaises(PolicyFailure):commit_audit(self.repo,self.base,'1.2.1')
    def test_reject_dirty_tree_instead_of_discarding(self):
        (self.repo/'file.py').write_text('DIRTY = 1\n')
        with self.assertRaisesRegex(PolicyFailure,'suja'):assert_clean(self.repo)
        self.assertIn('DIRTY', (self.repo/'file.py').read_text())
    def test_reject_base_not_ancestor(self):
        self.commit('fix(radar): impeça repetição de candidatura [#1.2.1]')
        with self.assertRaises(PolicyFailure):assert_base_ancestor(self.repo,git(self.repo,'rev-parse','HEAD'),self.base)
    def test_reject_suspected_secret_in_commit(self):
        (self.repo/'auth.json').write_text('not-a-real-secret')
        git(self.repo,'add','.');git(self.repo,'commit','-q','-m','fix(auth): impeça persistência de segredos [#1.2.1]')
        with self.assertRaisesRegex(PolicyFailure,'secreto'):commit_audit(self.repo,self.base,'1.2.1')

if __name__=='__main__':unittest.main()
