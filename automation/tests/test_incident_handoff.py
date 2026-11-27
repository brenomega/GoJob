"""Runner-only adversarial incident harness; never invokes a live Codex session."""
import json,subprocess,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
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
        self.assertTrue(d.reserve_repair_attempt(self.folder))
        self.assertFalse(d.repair_allowed(self.folder))
        state=d.read_json(self.folder/'repair_attempts.json',{})
        state['next_attempt_at']=time.time()-1
        d.save_json(self.folder/'repair_attempts.json',state)
        self.assertTrue(d.repair_allowed(self.folder))
        self.assertTrue(d.reserve_repair_attempt(self.folder))
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
             patch.object(d,'promote_approved_supervisor',return_value={'status':'DEPLOYED'}) as deploy:
            outcome=watch.check(self.repo,self.home)
            self.assertEqual(outcome['status'],'REPAIRED')
            repair.assert_called_once();deploy.assert_called_once()
            self.assertTrue(any((self.home/'incidents').glob('*/context.json')) is False) # mocked diagnostic
            self.assertTrue(any((self.home/'incidents').glob('*/watchdog_action.json')))

    def test_watchdog_shares_failed_supervisor_invocation_and_budget(self):
        from types import SimpleNamespace

        identity='a'*32
        result=SimpleNamespace(returncode=70,stdout='',stderr='runner crashed reading checkpoint')
        diagnosis={'decision':'REPAIR_RUNNER','cause':'invalid checkpoint read','plan':'correct reader',
                   'evidence':['reproduced failure on valid checkpoint']}
        with (patch.dict('os.environ',{'INVOCATION_ID':identity}),
              patch.object(s,'resume_runner',return_value=(result,'head1')),
              patch.object(s,'supervisor_triage',return_value=(diagnosis,{})),
              patch.object(s,'repair',side_effect=RuntimeError('synthetic transport outage')),
              patch.object(s,'notify')):
            self.assertEqual(s.main(['--repo',str(self.repo),'--home',str(self.home)]),70)
        session=d.read_json(self.home/'supervisor_session.json',{})
        original=session['incident_id']
        status={'ActiveState':'failed','Result':'exit-code','ExecMainStatus':'70',
                'InvocationID':identity,'ExecMainPID':str(session['pid']),
                'ExecMainStartTimestampMonotonic':str(session['observed_at']-1),
                'ExecMainExitTimestampMonotonic':str(session['observed_at']+1)}
        with (patch.object(watch,'service_status',return_value=status),
              patch.object(watch,'triage_event',return_value=(diagnosis,{})),
              patch.object(watch,'fallback_repair',side_effect=RuntimeError('synthetic outage')) as repair):
            shared=watch.check(self.repo,self.home)
            self.assertEqual(shared['status'],'REPAIR_COOLDOWN_OR_EXHAUSTED')
            self.assertEqual(shared['incident'],original)
            repair.assert_not_called()
            status['InvocationID']='b'*32
            new=watch.check(self.repo,self.home)
        self.assertNotEqual(new['incident'],original)
        repair.assert_called_once()
        budget=d.read_json(self.home/'incidents'/original/'repair_attempts.json',{})
        self.assertEqual(budget['attempts'],1)

    def test_watchdog_defers_failed_intent_and_reconciles_next_after_health_check(self):
        diagnosis={'decision':'REPAIR_RUNNER','cause':'runner bug','plan':'reconcile merge intent',
                   'evidence':['reviewed PR retained']}
        first,second='a'*14,'b'*14
        for ident in (first,second):
            folder=self.home/'incidents'/ident
            d.save_json(folder/'incident.json',{'id':ident,'repo':str(self.repo)})
            d.save_json(folder/'triage.json',diagnosis)
            d.save_json(self.home/'repairs'/f'{ident}-review-history.json',{'merge_intent':{'pr':3}})
        calls=[]

        def health():
            calls.append('health')
            return {'ActiveState':'inactive','Result':'success'}

        def repair(repo,home,ident,*_):
            calls.append(ident)
            if ident==first:
                raise RuntimeError('Base avançou enquanto o PR permaneceu aberto')
            return {'merge':'confirmed'}

        with (patch.object(watch,'service_status',side_effect=health),
              patch.object(watch,'fallback_repair',side_effect=repair),
              patch.object(d,'promote_approved_supervisor',return_value={'status':'DEPLOYED'})):
            self.assertEqual(watch.check(self.repo,self.home)['status'],'REPAIRED')
            self.assertEqual(watch.check(self.repo,self.home)['status'],'HEALTHY_OR_IDLE')
        self.assertEqual(calls,['health',first,second,'health'])
        self.assertTrue((self.home/'incidents'/second/'deployment.json').is_file())
        retry=d.read_json(self.home/'incidents'/first/'merge-resume.json',{})
        self.assertEqual(retry['attempts'],1)
        self.assertIn('Base avançou',retry['last_error'])

    def test_independent_supervisor_exit_76_is_diagnosed_not_blindly_ignored(self):
        from types import SimpleNamespace
        failed=SimpleNamespace(returncode=76,stdout='',stderr='Codex unavailable')
        recovered=SimpleNamespace(returncode=0,stdout='done',stderr='')
        with patch.object(s,'resume_runner',side_effect=[(failed,'head1'),(recovered,'head2')]),\
             patch.object(s,'supervisor_triage',return_value=({'decision':'REPAIR_RUNNER','cause':'PATH bug',
                     'evidence':['NVM'], 'plan':'persist PATH'},{})),\
             patch.object(s,'repair',return_value={'pr':5,'merge':{'oid':'sha'}}) as repair,\
             patch.object(d,'promote_approved_supervisor',return_value={'status':'DEPLOYED'}),\
             patch.object(s,'proactive_cycle',return_value={'status':'COOLDOWN'}),\
             patch.object(s,'maybe_evolve',return_value={'status':'NO_METRICS'}):
            outcome=s.main(['--repo',str(self.repo),'--home',str(self.home),'--max-repairs','1'])
            self.assertEqual(outcome,0)
            repair.assert_called_once()

    def test_watchdog_budget_is_stable_across_restarts_and_shares_supervisor_lock(self):
        diagnosis={'decision':'REPAIR_RUNNER','cause':'startup import failure',
                   'plan':'fix missing module','evidence':['clean checkout import fails']}
        def crash(*_):
            raise KeyboardInterrupt
        ids=[]
        with (patch.object(watch,'triage_event',return_value=(diagnosis,{})),
              patch.object(watch,'fallback_repair',side_effect=crash) as repair):
            for number in (1,2,3):
                d.save_json(self.home/'supervisor_session.json',
                            {'phase':'RUNNING','started_at':number,'pid':400+number})
                status={'ActiveState':'failed','Result':'exit-code','ExecMainStatus':'1',
                        'MainPID':str(400+number)}
                with patch.object(watch,'service_status',return_value=status):
                    if number==1:
                        with d.repair_lock(self.home,self.repo) as held:
                            self.assertTrue(held)
                            self.assertEqual(watch.check(self.repo,self.home)['status'],'REPAIR_BUSY')
                            repair.assert_not_called()
                    if number<=2:
                        with self.assertRaises(KeyboardInterrupt):
                            watch.check(self.repo,self.home)
                        incidents=list((self.home/'incidents').glob('*/repair_attempts.json'))
                        self.assertEqual(len(incidents),1)
                        state=d.read_json(incidents[0],{})
                        self.assertEqual(state['attempts'],number)
                        ids.append(incidents[0].parent.name)
                        state['next_attempt_at']=0
                        d.save_json(incidents[0],state)
                    else:
                        result=watch.check(self.repo,self.home)
                        self.assertEqual(result['status'],'REPAIR_COOLDOWN_OR_EXHAUSTED')
                        self.assertEqual(result['incident'],ids[0])
            self.assertEqual(repair.call_count,2)
            self.assertEqual(ids[0],ids[1])

    def test_proactive_false_positive_without_reproduction_is_follow_up_only(self):
        fake=lambda *_:{'decision':'REPAIR_RUNNER','summary':'unspecified',
                'evidence':['a claim'],'hypothesis':'could break','reproduction':'',
                'expected_failure':'unexplained','priority':'high','proposed_tests':[]}
        result=d.proactive_review(self.repo,self.home,agent_func=fake,runner_sha='sha')
        self.assertEqual(result['status'],'FOLLOW_UP')

if __name__=='__main__':unittest.main()
