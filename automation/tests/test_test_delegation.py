"""These test the RUNNER's agent delegation only, never GoJob application logic."""
import tempfile, unittest
from pathlib import Path
from unittest.mock import patch
from gojob_runner.runner import Runner
from gojob_runner.tools import PolicyFailure,is_product_test_path

HEAD='c'*40

class DelegatedTestOwnership(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.r=Runner(Path(self.tmp.name),Path(self.tmp.name)/'state',scm=object(),codex=object())
        self.r._prompt=lambda *a,**k:'Detached acceptance criteria only'
        self.task={'id':'1.2.1','title':'Implemente filtros','type':'feat',
                   'goal':'Filtros elegíveis','acceptance':['Rejeitar vaga fora de TI'],'dependencies':[]}
        self.agent_calls=[]
    def _simulate(self,response,changed=([],[])):
        self.r._agent=lambda *a,**k:(self.agent_calls.append((a[0],k.get('write'))), response)[1]
        with patch('gojob_runner.runner.product_code_changes',return_value=['src/filters.py']), \
             patch('gojob_runner.runner.git',return_value=HEAD), \
             patch('gojob_runner.runner.worktree_changes',side_effect=[([],[]),changed]), \
             patch('gojob_runner.runner.apply_commit_plan') as commit:
            return commit,self.r._delegate_product_tests(self.task,Path(self.tmp.name),HEAD)
    def test_independent_agent_invoked_and_no_product_tests_authored_by_runner(self):
        report={'status':'DONE','commit_plan':[],
                'existing_test_evidence':['tests/test_filters.py::test_non_tech'],
                'failure_modes':['Veterinária não pode ser vaga de TI']}
        commit,_=self._simulate(report)
        self.assertEqual(self.agent_calls,[('independent-test-engineer',True)])
        self.assertEqual(self.r.entry('1.2.1')['test_head'],HEAD)
        commit.assert_not_called()
    def test_noop_without_independent_oracle_is_rejected(self):
        with self.assertRaisesRegex(PolicyFailure,'oráculos existentes'):
            self._simulate({'status':'DONE','commit_plan':[], 'existing_test_evidence':[], 'failure_modes':[]})
    def test_test_engineer_cannot_modify_product(self):
        with self.assertRaisesRegex(PolicyFailure,'fora dos caminhos'):
            self._simulate({'status':'DONE','commit_plan':[], 'existing_test_evidence':[], 'failure_modes':[]},([],['src/filters.py']))
    def test_scaffold_test_is_protected_and_separate_from_product_tests(self):
        self.assertTrue(is_product_test_path('tests/test_my_product.py'))
        self.assertTrue(is_product_test_path('src/test/java/FooTest.java'))
        self.assertFalse(is_product_test_path('automation/gojob_runner/runner.py'))
        with self.assertRaisesRegex(PolicyFailure,'fora dos caminhos'):
            self._simulate({'status':'DONE','commit_plan':[], 'existing_test_evidence':[], 'failure_modes':[]},([],['tests/test_bootstrap.py']))
    def test_docs_only_does_not_trigger_test_engineer(self):
        self.r._agent=lambda *a,**k:self.fail('No agent for docs only')
        with patch('gojob_runner.runner.product_code_changes',return_value=[]):
            self.assertIsNone(self.r._delegate_product_tests(self.task,Path(self.tmp.name),HEAD))

if __name__=='__main__':unittest.main()
