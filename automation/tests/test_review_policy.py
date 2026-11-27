"""Contract tests for the runner's reviewer feedback, not for GoJob product."""
import unittest
from gojob_runner.review_policy import disposition,review_feedback,MAX_REVIEW_ROUNDS

class ReviewPolicy(unittest.TestCase):
    def setUp(self):
        self.s={'decision':'STOP','rationale':'A direção precisa de ajuste concreto',
                'findings':['A solução não mede recall de programas de verão'],
                'north_star_impact':'Reduz cobertura de vagas TI',
                'opportunity_cost':'Deixa fontes relevantes de fora',
                'alternative':'Medir corpus independente primeiro'}
        self.t={'decision':'ACCEPT','rationale':'Código compila e testes exercitados',
                'findings':[],'real_evidence':['diff real'],'tests_assessed':['suite existente']}
    def test_stop_actionable_is_not_a_terminal_failure(self):
        self.assertEqual(disposition(self.s,self.t).action,'REPAIR')
    def test_block_actionable_is_not_a_terminal_failure(self):
        self.s['decision']='PASS';self.s['findings']=[]
        self.t['decision']='BLOCK';self.t['findings']=['Corrigir caso de recuperação após falha']
        self.assertEqual(disposition(self.s,self.t).action,'REPAIR')
    def test_human_only_obligation_requires_explicit_reason(self):
        self.s['blocking_kind']='HUMAN_DECISION'
        self.assertEqual(disposition(self.s,self.t).action,'NEEDS_USER')
    def test_missing_actionable_findings_do_not_trigger_blind_repair(self):
        self.s['findings']=[]
        self.assertEqual(disposition(self.s,self.t).action,'NEEDS_USER')
    def test_full_feedback_contains_strategy_not_just_findings(self):
        payload=review_feedback(self.s,self.t)
        self.assertEqual(payload['strategic']['alternative'],self.s['alternative'])
        self.assertEqual(payload['technical']['tests_assessed'],['suite existente'])
    def test_three_round_budget(self):
        self.assertEqual(MAX_REVIEW_ROUNDS,3)

if __name__=='__main__':unittest.main()
