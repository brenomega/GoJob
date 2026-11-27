"""Runner-only adversarial incident harness; never invokes a live Codex session."""
import json,subprocess,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
try:
    from automation.supervisor import recovery_dispatch as d, recovery_context as ctx, stable_supervisor as s
    from automation.supervisor import rescue_watchdog as watch
except ImportError:
    import recovery_dispatch as d
    import recovery_context as ctx
    import stable_supervisor as s
    import rescue_watchdog as watch

class IncidentRecovery(unittest.TestCase):
    def setUp(self):
        self.t=tempfile.TemporaryDirectory();self.addCleanup(self.t.cleanup)
        self.home=Path(self.t.name)/'data';self.home.mkdir()
        self.repo=Path(self.t.name)/'repo';self.repo.mkdir()
        subprocess.run(['git','init','-q',str(self.repo)],check=True)
        subprocess.run(['git','-C',str(self.repo),'config','user.name','Synthetic'],check=True)
        subprocess.run(['git','-C',str(self.repo),'config','user.email','synthetic@example.test'],check=True)
        for path in ctx.DOCUMENTS:
            f=self.repo/path;f.parent.mkdir(parents=True,exist_ok=True)
            f.write_text('# '+path+'\nNorth Star of GoJob and runner decisions.\n')
        subprocess.run(['git','-C',str(self.repo),'add','.'],check=True)
        subprocess.run(['git','-C',str(self.repo),'commit','-qm','baseline'],check=True)
        key=__import__('hashlib').sha256(str(self.repo.resolve()).encode()).hexdigest()[:16]
        self.state_root=self.home/'state'/key
        self.state_root.mkdir(parents=True)
        (self.state_root/'state.json').write_text(json.dumps({'repo_identity':str(self.repo),
            'tasks':{'0.1.2':{'stage':'BLOCKED','head':'deadbeef','review_history':[
                {'finding':'The developer did not inspect the PPO code'}],
                'reason':'Codex CLI absent from systemd but present in shell'}}}))
        (self.state_root/'journal.jsonl').write_text(json.dumps({
            'event':'BLOCKED','task':'0.1.2','time':1,'details':{'stage':'BLOCKED'}})+'\n')
        self.folder=self.home/'incidents'/'case';self.folder.mkdir(parents=True)
        self.info={'id':'case','kind':'external_dependency','error':'ExternalFailure: Codex CLI absent',
                   'repo':str(self.repo)}

    def test_dossier_is_private_and_conveys_context_not_only_exception(self):
        bundle,digest=ctx.gather(self.home,self.repo,'external_dependency',self.info['error'],self.folder)
        self.assertIn('0.1.2',bundle['task_state'])
        self.assertIn('BLOCKED',bundle['task_state']['0.1.2']['stage'])
        self.assertIn('docs/PRODUCT_NORTH_STAR.md',bundle['documents'])
        self.assertIn('docs/USER_EXPERIENCE.md',bundle['documents'])
        self.assertIn('docs/TESTING_POLICY.md',bundle['documents'])
        self.assertTrue(bundle['recent_journal'])
        self.assertEqual((self.folder/'context.sha256').read_text().strip(),digest)
        self.assertEqual((self.folder/'context.json').stat().st_mode & 0o077,0)
        self.assertIn('Codex CLI absent',ctx.prompt_context(bundle,digest))

    def test_authoritative_documents_retain_the_beginning_not_log_tail(self):
        target=self.repo/'docs/PRODUCT_NORTH_STAR.md'
        target.write_text('START_OF_NORTH_STAR\n'+'A'*7400+'\nEND_OF_NORTH_STAR\n')
        subprocess.run(['git','-C',str(self.repo),'add','.'],check=True)
        subprocess.run(['git','-C',str(self.repo),'commit','-qm','north star'],check=True)
        subprocess.run(['git','-C',str(self.repo),'update-ref','refs/remotes/origin/develop','HEAD'],check=True)
        bundle,_=ctx.gather(self.home,self.repo,'diagnostic','failure',self.folder)
        doc=bundle['documents']['docs/PRODUCT_NORTH_STAR.md']['text']
        self.assertTrue(doc.startswith('START_OF_NORTH_STAR'))
        self.assertNotIn('END_OF_NORTH_STAR',doc)

    def test_error_labeled_external_can_be_routed_to_runner_repair(self):
        prompts=[]
        def model(repo,prompt,schema,write):
            prompts.append(prompt)
            self.assertFalse(write)
            return {'decision':'REPAIR_RUNNER','cause':'systemd PATH not transferred',
                    'evidence':['Codex installed on interactive NVM but missing from service'],
                    'plan':'persist verified executable path in unit','confidence':'high',
                    'risk_if_wrong':'could need user login'}
        out,_=d.triage_event(self.repo,self.home,self.folder,self.info,agent_func=model)
        self.assertEqual(out['decision'],'REPAIR_RUNNER')
        self.assertIn('PRODUCT_NORTH_STAR',prompts[0])
        self.assertIn('review_history',prompts[0])
        self.assertTrue((self.folder/'triage.json').is_file())
        # Same causal state: do not bill a second model call.
        out,_=d.triage_event(self.repo,self.home,self.folder,self.info,agent_func=lambda *_:self.fail('duplicate agent'))
        self.assertEqual(out['decision'],'REPAIR_RUNNER')

    def test_human_veto_cannot_be_overridden_by_a_model(self):
        info={**self.info,'error':'PolicyFailure: revoked approval'}
        result={'decision':'REPAIR_RUNNER','cause':'repair suggested','evidence':['synthetic'],
                'plan':'ignore user','confidence':'low','risk_if_wrong':'security'}
        decision,_=d.triage_event(self.repo,self.home,self.folder,info,agent_func=lambda *_:result)
        self.assertEqual(decision['decision'],'POLICY_STOP')

    def test_codex_unavailable_preserves_dossier_and_queues_retry(self):
        def error(*args):raise FileNotFoundError('codex not in path')
        decision,_=d.triage_event(self.repo,self.home,self.folder,self.info,agent_func=error)
        self.assertEqual(decision['decision'],'DEFERRED')
        self.assertTrue((self.folder/'context.json').is_file())
        state=d.read_json(self.folder/'triage_state.json',{})
        self.assertGreater(state['next_attempt_at'],time.time())
        again,_=d.triage_event(self.repo,self.home,self.folder,self.info,
                                agent_func=lambda *_:self.fail('retry before cooldown'))
        self.assertEqual(again['decision'],'DEFERRED')

    def test_bounded_repair_allows_future_retry_not_permanent_failed_marker(self):
        self.assertTrue(d.repair_allowed(self.folder))
        d.record_repair_attempt(self.folder,'FAILED','temporary network')
        self.assertFalse(d.repair_allowed(self.folder))
        state=d.read_json(self.folder/'repair_attempts.json',{})
        state['next_attempt_at']=time.time()-1
        d.save_json(self.folder/'repair_attempts.json',state)
        self.assertTrue(d.repair_allowed(self.folder))
        d.record_repair_attempt(self.folder,'FAILED','again')
        self.assertFalse(d.repair_allowed(self.folder,now=time.time()+100000))

    def test_proactive_audit_is_independent_and_deduplicated(self):
        def auditor(*args):
            return {'decision':'REPAIR_RUNNER','summary':'False green after task blocked',
                'evidence':['known stage and state'], 'hypothesis':'exit code hides BLOCKED',
                'reproduction':'create BLOCKED state then run cycle',
                'expected_failure':'return success with unresolved work',
                'priority':'high','proposed_tests':['assert blocker observed']}
        result=d.proactive_review(self.repo,self.home,agent_func=auditor,runner_sha='sha',minimum_interval=86400)
        self.assertEqual(result['status'],'REPAIR_RUNNER')
        self.assertEqual(d.proactive_review(self.repo,self.home,agent_func=lambda *_:self.fail('duplicate'),
                      runner_sha='sha')['status'],'COOLDOWN')

    def test_watchdog_success_idle_does_not_trigger_repair(self):
        with patch.object(watch,'service_status',return_value={'ActiveState':'inactive','Result':'success'}),\
             patch.object(watch,'triage_event') as triage:
            self.assertEqual(watch.check(self.repo,self.home)['status'],'HEALTHY_OR_IDLE')
            triage.assert_not_called()

    def test_watchdog_recovers_crash_even_if_supervisor_did_not_write_incident(self):
        diagnosed={'decision':'REPAIR_RUNNER','cause':'supervisor parse failure','plan':'fix syntax',
                   'evidence':['SyntaxError']}
        with patch.object(watch,'service_status',return_value={'ActiveState':'failed','Result':'exit-code','ExecMainStatus':'1'}),\
             patch.object(watch,'triage_event',return_value=(diagnosed,{})),\
             patch.object(watch,'fallback_repair',return_value={'pr':44,'merge':{'oid':'synthetic'}}) as repair,\
             patch.object(watch,'promote_approved_supervisor',return_value={'status':'DEPLOYED'}) as deploy:
            outcome=watch.check(self.repo,self.home)
            self.assertEqual(outcome['status'],'REPAIRED')
            repair.assert_called_once();deploy.assert_called_once()
            self.assertTrue(any((self.home/'incidents').glob('*/context.json')) is False) # mocked diagnostic
            self.assertTrue(any((self.home/'incidents').glob('*/watchdog_action.json')))

    def test_independent_supervisor_exit_76_is_diagnosed_not_blindly_ignored(self):
        from types import SimpleNamespace
        failed=SimpleNamespace(returncode=76,stdout='',stderr='Codex unavailable')
        recovered=SimpleNamespace(returncode=0,stdout='done',stderr='')
        with patch.object(s,'resume_runner',side_effect=[(failed,'head1'),(recovered,'head2')]),\
             patch.object(s,'supervisor_triage',return_value=({'decision':'REPAIR_RUNNER','cause':'PATH bug',
                     'evidence':['NVM'], 'plan':'persist PATH'},{})),\
             patch.object(s,'repair',return_value={'pr':5,'merge':{'oid':'sha'}}) as repair,\
             patch.object(s,'promote_approved_supervisor',return_value={'status':'DEPLOYED'}),\
             patch.object(s,'proactive_cycle',return_value={'status':'COOLDOWN'}),\
             patch.object(s,'maybe_evolve',return_value={'status':'NO_METRICS'}):
            outcome=s.main(['--repo',str(self.repo),'--home',str(self.home),'--max-repairs','1'])
            self.assertEqual(outcome,0)
            repair.assert_called_once()

    def test_watchdog_can_repair_when_current_supervisor_has_syntax_error(self):
        stable=self.home/'stable';stable.mkdir()
        (stable/'stable_supervisor.py').write_text('def this is broken syntax:')
        fallback=stable/'stable_supervisor.previous.123.py'
        fallback.write_text('def repair(repo,home,inc,info):\n    return {"pr":31,"merge":{"oid":"abc"}}\n')
        # It is the watchdog script, not the broken supervisor, that imports
        # the last-known-good repair engine.
        result=watch.fallback_repair(self.repo,self.home,'case',self.info,stable)
        self.assertEqual(result['pr'],31)
        self.assertIn('broken syntax',(stable/'stable_supervisor.py').read_text())

    def test_watchdog_tries_next_engine_if_a_candidate_cannot_be_imported(self):
        stable=self.home/'stable';stable.mkdir()
        (stable/'stable_supervisor.previous.222.py').write_text('def repair(repo,home,inc,info):\n    return {"pr":17}\n')
        (stable/'stable_supervisor.previous.333.py').write_text('broken python syntax here')
        result=watch.fallback_repair(self.repo,self.home,'case',self.info,stable)
        self.assertEqual(result['pr'],17)

    def test_promotion_rejects_unreviewed_sha_and_runs_clean_canary(self):
        import shutil,hashlib
        root=Path(__file__).resolve().parents[1]
        remote=Path(self.t.name)/'remote.git'
        subprocess.run(['git','init','--bare','-q',str(remote)],check=True)
        subprocess.run(['git','-C',str(self.repo),'remote','add','origin',str(remote)],check=True)
        folder=self.repo/'automation/supervisor';folder.mkdir(parents=True,exist_ok=True)
        for name in ('stable_supervisor.py','recovery_context.py','recovery_dispatch.py','rescue_watchdog.py','schema_protocol.py'):
            src=(root/'gojob_runner/schema_protocol.py') if name=='schema_protocol.py' else root/name
            if not src.exists():src=root/'supervisor'/name
            shutil.copy2(src,folder/name)
        (self.repo/'RUNNER_VERSION').write_text('3.4.0\n')
        subprocess.run(['git','-C',str(self.repo),'add','.'],check=True)
        subprocess.run(['git','-C',str(self.repo),'commit','-qm','approved runner'],check=True)
        subprocess.run(['git','-C',str(self.repo),'branch','-M','develop'],check=True)
        subprocess.run(['git','-C',str(self.repo),'push','-u','origin','develop'],check=True)
        sha=subprocess.check_output(['git','-C',str(self.repo),'rev-parse','HEAD'],text=True).strip()
        stable=self.home/'stable';stable.mkdir()
        (stable/'stable_supervisor.py').write_text('old=1\n')
        with self.assertRaisesRegex(RuntimeError,'Develop moved'):
            d.promote_approved_supervisor(self.repo,self.home,expected_merge='different_SHA')
        self.assertEqual((stable/'stable_supervisor.py').read_text(),'old=1\n')
        result=d.promote_approved_supervisor(self.repo,self.home,expected_merge={'oid':sha})
        self.assertEqual(result['status'],'DEPLOYED')
        self.assertIn('def main(', (stable/'stable_supervisor.py').read_text())
        self.assertEqual(d.promote_approved_supervisor(self.repo,self.home,expected_merge=sha)['status'],
                         'ALREADY_CURRENT')

    def test_proactive_false_positive_without_reproduction_is_follow_up_only(self):
        fake=lambda *_:{'decision':'REPAIR_RUNNER','summary':'unspecified',
                'evidence':['a claim'],'hypothesis':'could break','reproduction':'',
                'expected_failure':'unexplained','priority':'high','proposed_tests':[]}
        result=d.proactive_review(self.repo,self.home,agent_func=fake,runner_sha='sha')
        self.assertEqual(result['status'],'FOLLOW_UP')

if __name__=='__main__':unittest.main()
