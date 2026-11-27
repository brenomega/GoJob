"""Real Git commit broker: agent edits file, controller commits scoped, reviewable plan."""
import subprocess,tempfile,unittest
from pathlib import Path
from gojob_runner.tools import apply_commit_plan,PolicyFailure,worktree_changes

def git(path,*args):
    p=subprocess.run(['git',*args],cwd=path,text=True,capture_output=True)
    if p.returncode:raise RuntimeError(p.stderr)
    return p.stdout.strip()

class CommitBroker(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.repo=Path(self.tmp.name)
        git(self.repo,'init','-q');git(self.repo,'config','user.name','Test');git(self.repo,'config','user.email','test@example.com')
        (self.repo/'README.md').write_text('Original\n');git(self.repo,'add','.');git(self.repo,'commit','-qm','base')
    def tearDown(self):self.tmp.cleanup()
    def test_controller_commits_developer_file_without_agent_git_access(self):
        (self.repo/'code.py').write_text('def answer():\n    return 42\n')
        (self.repo/'test_code.py').write_text('from code import answer\nassert answer()==42\n')
        report={'status':'DONE','commit_plan':[
            {'message':'feat(core): implemente resposta determinística [#1.2.1]','paths':['code.py']},
            {'message':'test(core): teste o resultado concreto [#1.2.1]','paths':['test_code.py']}]}
        commits=apply_commit_plan(self.repo,'1.2.1',report)
        self.assertEqual(len(commits),2)
        self.assertEqual(git(self.repo,'status','--porcelain'),'')
        self.assertIn('[#1.2.1]',git(self.repo,'show','-s','--format=%s'))
    def test_incomplete_plan_rejected_before_partial_commit(self):
        (self.repo/'first.py').write_text('x=1\n');(self.repo/'second.py').write_text('y=2\n')
        head=git(self.repo,'rev-parse','HEAD')
        report={'status':'DONE','commit_plan':[{'message':'feat(core): implemente primeiro arquivo [#1.2.1]',
                                                'paths':['first.py']}]}
        with self.assertRaisesRegex(PolicyFailure,'não corresponde exatamente'):
            apply_commit_plan(self.repo,'1.2.1',report)
        self.assertEqual(git(self.repo,'rev-parse','HEAD'),head)
    def test_traversal_path_denied_before_git_stage(self):
        (self.repo/'ok.py').write_text('x=1\n')
        report={'status':'DONE','commit_plan':[{'message':'fix(core): corrija validação de path [#1.2.1]',
                                                'paths':['../outside.py']}]}
        with self.assertRaises(PolicyFailure):apply_commit_plan(self.repo,'1.2.1',report)
    def test_secrets_not_committed(self):
        (self.repo/'.env').write_text('MY_SECRET=fixture')
        report={'status':'DONE','commit_plan':[{'message':'fix(auth): valide credenciais seguras [#1.2.1]',
                                                'paths':['.env']}]}
        with self.assertRaises(PolicyFailure):apply_commit_plan(self.repo,'1.2.1',report)
    def test_missing_commit_plan_not_accepted(self):
        (self.repo/'code.py').write_text('x=1\n')
        with self.assertRaises(PolicyFailure):apply_commit_plan(self.repo,'1.2.1',{'status':'DONE'})

    def test_runner_repair_preserves_index_with_preexisting_out_of_scope_file(self):
        from stable_supervisor import apply_repair_commit_plan

        (self.repo/'README.md').write_text('Staged by a different owner.\n')
        git(self.repo,'add','README.md')
        target=self.repo/'automation/supervisor/example.py'
        target.parent.mkdir(parents=True)
        target.write_text('value = 42\n')
        head=git(self.repo,'rev-parse','HEAD')
        report={'status':'DONE','commit_plan':[{
            'message':'fix(runner): corrija retomada do supervisor [#0.9.0]',
            'paths':['automation/supervisor/example.py']}]}
        with self.assertRaisesRegex(PolicyFailure,'staged'):
            apply_repair_commit_plan(self.repo,report)
        self.assertEqual(git(self.repo,'rev-parse','HEAD'),head)
        self.assertEqual(worktree_changes(self.repo),(['README.md'],['automation/supervisor/example.py']))

if __name__=='__main__':unittest.main()
