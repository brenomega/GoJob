#!/usr/bin/env python3
"""Stable, standalone GoJob RUNNER supervisor v3.0.0 (stdlib only).

Install OUTSIDE product checkout. It can restart the runner from a pinned remote
commit; on a reproducible RUNNER crash it attempts a reviewed repair PR.
No auto-repair for an ordinary task failure, policy refusal, quota, or lost auth.
"""
from __future__ import annotations
import argparse, fcntl, hashlib, json, os, re, subprocess, sys, tempfile, time
from pathlib import Path

MODEL='gpt-6.2-sol';EFFORT='high'

def redact(s):
    s=str(s)
    s=re.sub(r'(?i)(token|password|secret|api[_-]?key)\s*[:=]\s*\S+',r'\1=[REDACTED]',s)
    s=re.sub(r'gh[pousr]_[A-Za-z0-9_\-]{12,}', '[REDACTED_GITHUB_TOKEN]',s)
    s=re.sub(r'\bgsk_[A-Za-z0-9_\-]{12,}', '[REDACTED_PROVIDER_TOKEN]',s)
    return s[-3000:]

def run(argv,cwd=None,timeout=180,check=True,env=None):
    try:r=subprocess.run(argv,cwd=cwd,env=env,text=True,capture_output=True,timeout=timeout)
    except (OSError,subprocess.TimeoutExpired) as e:raise RuntimeError(type(e).__name__+': '+str(e)[:400]) from e
    if check and r.returncode:raise RuntimeError(redact(r.stderr or r.stdout))
    return r

def git(repo,*args,timeout=180):return run(['git',*args],cwd=repo,timeout=timeout).stdout.strip()

def gh(repo,*args,timeout=180):return run(['gh',*args],cwd=repo,timeout=timeout).stdout.strip()

def atomic_json(path,data):
    path=Path(path);path.parent.mkdir(parents=True,mode=0o700,exist_ok=True)
    with tempfile.NamedTemporaryFile('w',dir=path.parent,prefix='.tmp-',delete=False) as f:
        os.fchmod(f.fileno(),0o600);json.dump(data,f,indent=2,ensure_ascii=False)
        f.flush();os.fsync(f.fileno());temp=f.name
    os.replace(temp,path)

def incident(home,repo,error,kind):
    fingerprint=hashlib.sha256((kind+'|'+str(error)).encode()).hexdigest()[:14]
    folder=Path(home)/'incidents'/fingerprint
    folder.mkdir(parents=True,mode=0o700,exist_ok=True)
    obj={'id':fingerprint,'time':time.time(),'kind':kind,'error':redact(error),
         'repo':str(Path(repo).resolve())}
    for field,cmd in {'head':['git','rev-parse','HEAD'],'branch':['git','branch','--show-current']}.items():
        try:obj[field]=run(cmd,cwd=repo,timeout=20).stdout.strip()[:100]
        except Exception:obj[field]='unknown'
    atomic_json(folder/'incident.json',obj)
    return folder,obj

def installed_runner(repo,home):
    """Code at immutable origin/develop SHA; never run code from developer's dirty working tree."""
    repo=Path(repo).resolve();git(repo,'fetch','origin','develop',timeout=180)
    sha=git(repo,'rev-parse','origin/develop')
    folder=Path(home)/'checkouts'/sha[:16]
    folder.parent.mkdir(parents=True,exist_ok=True)
    if not folder.exists():git(repo,'worktree','add','--detach',str(folder),sha)
    if git(folder,'rev-parse','HEAD')!=sha or git(folder,'status','--porcelain'):
        raise RuntimeError('Checkout de execução divergente/sujo, preservar e bloquear')
    return folder,sha

def agent(repo,prompt,schema,write,timeout=1800):
    schema=Path(schema).resolve()
    with tempfile.TemporaryDirectory(prefix='gojob-runner-repair-') as tmp:
        result=Path(tmp)/'answer.json'
        env=os.environ.copy()
        for key in ('GH_TOKEN','GITHUB_TOKEN','OPENAI_API_KEY','CODEX_API_KEY','TELEGRAM_BOT_TOKEN','GOJOB_VAULT_KEY'):
            env.pop(key,None)
        cmd=['codex','exec','--ephemeral','-C',str(repo),'--model',MODEL,'--config','model_reasoning_effort=high',
             '--sandbox','workspace-write' if write else 'read-only','--output-schema',str(schema),
             '--output-last-message',str(result),prompt]
        run(cmd,cwd=repo,env=env,timeout=timeout)
        if not result.exists():raise RuntimeError('Agente não produziu relatório')
        return json.loads(result.read_text())

def apply_repair_commit_plan(repo,report):
    """Host-owned commits for sandboxed Codex, limited to runner files."""
    repo=Path(repo)
    if report.get('status')!='DONE':raise RuntimeError('Repairer not done')
    changed=git(repo,'diff','--name-only').splitlines()+git(repo,'ls-files','--others','--exclude-standard').splitlines()
    changed=set(changed)
    plan=report.get('commit_plan',[])
    if not isinstance(plan,list) or not 1<=len(plan)<=8:raise RuntimeError('Commit plan ausente')
    allowed=('automation/gojob_runner/','automation/tests/','automation/supervisor/','docs/RUNNER_','RUNNER_VERSION','CHANGELOG_RUNNER.md')
    planned=[]
    for part in plan:
        message=part.get('message','')
        if not re.fullmatch(r'(feat|fix|refactor|test|docs|chore|perf|ci)\([a-z0-9_-]+\): .{8,160} \[#0\.9\.0\]',message):
            raise RuntimeError('Commit do reparo fora da convenção EAP')
        paths=part.get('paths')
        if not isinstance(paths,list) or not paths:raise RuntimeError('Plano de reparo sem paths')
        for path in paths:
            if (not isinstance(path,str) or not path.startswith(allowed) or '..' in Path(path).parts
                or not (repo/path).resolve().is_relative_to(repo.resolve())):
                raise RuntimeError('Reparo alteraria fora do escopo')
            planned.append(path)
    if set(planned)!=changed or len(set(planned))!=len(planned):
        raise RuntimeError('Plano do reparo não corresponde ao diff')
    for part in plan:
        git(repo,'add','--',*part['paths'])
        git(repo,'diff','--cached','--check')
        git(repo,'commit','-m',part['message'])
    if git(repo,'status','--porcelain'):raise RuntimeError('Reparo deixou diff após commits')
    return git(repo,'rev-parse','HEAD')

def resume_runner(repo,tasks,home,task_id=None,timeout=14400):
    checkout,sha=installed_runner(repo,home)
    env=os.environ.copy();env['PYTHONPATH']=str(checkout/'automation')
    repo_key=hashlib.sha256(str(Path(repo).resolve()).encode()).hexdigest()[:16]
    task_path=Path(tasks)
    if not task_path.is_absolute():task_path=checkout/task_path
    cmd=[sys.executable,'-B','-m','gojob_runner.runner','--repo',str(Path(repo).resolve()),
         '--tasks',str(task_path),'--state-root',str(Path(home)/'state'/repo_key)]
    if task_id:cmd+=['--task-id',task_id]
    p=run(cmd,cwd=checkout,env=env,timeout=timeout,check=False)
    return p,sha

def exact_version(base,current,impact):
    from itertools import zip_longest
    import re as regex
    def triple(text):
        if not regex.fullmatch(r'\d+\.\d+\.\d+',text.strip()):raise RuntimeError('Versão inválida')
        return tuple(map(int,text.strip().split('.')))
    a,b,c=triple(base);act=triple(current)
    desired={'bug':(a,b,c+1),'procedural':(a,b+1,0),'feature':(a+1,0,0)}.get(impact)
    if desired is None or act!=desired:raise RuntimeError(f'Versão runner incorreta para {impact}: {base} -> {current}')
    return True

def issue_for_incident(repo,ident,error):
    title='[RUNNER] Corrija incidente '+ident
    entries=json.loads(gh(repo,'issue','list','--state','all','--limit','200','--json','number,title,state'))
    found=[x for x in entries if x['title']==title]
    if len(found)>1:raise RuntimeError('Issues duplicadas do incidente')
    if found:
        if found[0]['state']!='OPEN':raise RuntimeError('Issue já encerrada, precisa reconciliação')
        return int(found[0]['number'])
    url=gh(repo,'issue','create','--title',title,'--body','Runner incident '+ident+'\n\nErro sanitizado: '+redact(error))
    m=re.search(r'/issues/(\d+)',url)
    if not m:raise RuntimeError('Resultado da criação de Issue incerto')
    return int(m.group(1))

def wait_ci(repo,pr,head,base,seconds=900):
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        snap=json.loads(gh(repo,'pr','view',str(pr),'--json','headRefOid,baseRefOid,state'))
        if snap['state']!='OPEN' or snap['headRefOid']!=head or snap['baseRefOid']!=base:
            raise RuntimeError('CI PR alterado; não certificar parecer antigo')
        p=run(['gh','pr','checks',str(pr),'--json','name,bucket'],cwd=repo,check=False)
        try:checks=json.loads(p.stdout)
        except ValueError:checks=[]
        if checks:
            statuses=[str(c.get('bucket','')).lower() for c in checks]
            if any(s in ('fail','cancel') for s in statuses):raise RuntimeError('CI falhou no reparo')
            if all(s=='pass' for s in statuses):return True
        time.sleep(15)
    raise RuntimeError('CI pendente, preservar PR')

def repair(repo,home,inc,info):
    """A separate repairer can only change runner code and its tests/docs, not product."""
    repo=Path(repo).resolve()
    git(repo,'fetch','origin','develop')
    base=git(repo,'rev-parse','origin/develop')
    branch='fix/0.9.0-runner-'+inc
    folder=Path(home)/'repairs'/inc
    folder.parent.mkdir(parents=True,exist_ok=True)
    if not folder.exists():
        if git(repo,'branch','--list',branch):git(repo,'worktree','add',str(folder),branch)
        else:git(repo,'worktree','add','-b',branch,str(folder),'origin/develop')
    if git(folder,'branch','--show-current')!=branch:raise RuntimeError('Worktree de reparo mudou')
    if git(folder,'status','--porcelain'):raise RuntimeError('Reparo existente com worktree suja; preservar e bloquear')
    issue=issue_for_incident(folder,inc,info['error'])
    schema=folder/'automation/gojob_runner/schemas/repair.json'
    tech=folder/'automation/gojob_runner/schemas/technical.json'
    strategy=folder/'automation/gojob_runner/schemas/strategic.json'
    if git(folder,'rev-list','--count',f'{base}..HEAD')=='0':
        prompt=('PAPEL RUNNER_REPAIRER, distinto do desenvolvedor de produto. Corrija somente defeito comprovado do runner. '
             'Leia docs de constituição, incidentes e testes. Preserve state, git histórico, segredos e perfis. '
             'Não altere produto; nunca reset/rebase/force-push. Acrescente regressão causal e/ou teste adversarial, '
             'sem asserts fracos nem testes redundantes. Atualize RUNNER_VERSION conforme natureza da correção: '
             'bug isolado patch y; procedimento transversal minor x; capacidade nova major. Atualize CHANGELOG_RUNNER.md. '
             'Edite os arquivos e devolva commit_plan de commits lógicos formato tipo(escopo): imperativo português [#0.9.0]. O controlador executa git commit. Não faça PR/merge/push. '
             f'BASE={base} INCIDENTE={json.dumps(info,ensure_ascii=False)}')
        dev=agent(folder,prompt,schema,write=True)
        if dev.get('status')!='DONE':raise RuntimeError('Reparador bloqueado')
        if git(folder,'status','--porcelain'):apply_repair_commit_plan(folder,dev)
    if git(folder,'status','--porcelain'):raise RuntimeError('Reparador deixou arquivos sem commit')
    head=git(folder,'rev-parse','HEAD')
    if head==base:raise RuntimeError('Reparo sem alterações')
    changes=git(folder,'diff','--name-only',f'{base}..HEAD').splitlines()
    allowed=('automation/gojob_runner/','automation/tests/','docs/RUNNER_','RUNNER_VERSION','CHANGELOG_RUNNER.md')
    if any(not p.startswith(allowed) for p in changes):
        raise RuntimeError('Reparo tocou no produto ou em arquivo fora do escopo')
    old=git(folder,'show',f'{base}:RUNNER_VERSION')
    current=(folder/'RUNNER_VERSION').read_text().strip()
    # Classify from submitted changed files with codex intent and separate independent review.
    impact=dev.get('impact','bug') if 'dev' in locals() else 'bug'
    if impact=='feature':raise RuntimeError('Nova funcionalidade do runner exige Issue planejada, não autorreparo')
    # The repair may explicitly declare feature; major changes are never promoted without human opt-in.
    exact_version(old,current,impact)
    env=os.environ.copy();env['PYTHONPATH']=str(folder/'automation')
    for cmd in ([sys.executable,'-B','-m','unittest','discover','-s','automation/tests','-p','test_*.py','-v'],
                [sys.executable,'-B','-m','compileall','-q','automation/gojob_runner'],
                [sys.executable,'-B','-m','gojob_runner.gates']):
        run(cmd,cwd=folder,timeout=600,env=env)
    policy=('Não altere arquivos. Head/base exatos. Avalie testes capazes de detectar falhas reais, '
            'correção causal, preservação de estado e limites de segurança. Se incerteza REVISE.')
    score=agent(folder,f'REVIEWER ESTRATÉGICO independente de reparo do RUNNER. BASE {base}, HEAD {head}. '+policy,
                strategy,write=False)
    check=agent(folder,f'REVIEWER TÉCNICO independente de reparo do RUNNER. BASE {base}, HEAD {head}. '+policy,
                tech,write=False)
    if not (score.get('base_sha')==base and score.get('head_sha')==head and score.get('decision')=='PASS'
            and check.get('base_sha')==base and check.get('head_sha')==head and check.get('decision')=='ACCEPT'):
        raise RuntimeError('Revisor bloqueou o reparo do runner')
    git(folder,'push','-u','origin',branch,timeout=240)
    prs=json.loads(gh(folder,'pr','list','--state','all','--limit','200','--json','headRefName,number,state'))
    matched=[x for x in prs if x['headRefName']==branch]
    if len(matched)>1:raise RuntimeError('PRs duplicados de reparo')
    if matched:pr=int(matched[0]['number'])
    else:
        url=gh(folder,'pr','create','--base','develop','--head',branch,'--title',f'[Runner] Corrija incidente {inc}',
            '--body',f'Refs #{issue}\nRUNNER SHA {head}. Revisão independente pelo harness; não APPROVE nativo.')
        m=re.search(r'/pull/(\d+)',url)
        if not m:raise RuntimeError('PR criado? status incerto')
        pr=int(m.group(1))
    snap=json.loads(gh(folder,'pr','view',str(pr),'--json','state,headRefOid,baseRefOid,headRefName'))
    if snap['state']!='OPEN' or snap['headRefOid']!=head or snap['baseRefOid']!=base or snap['headRefName']!=branch:
        raise RuntimeError('Snapshot de PR de reparo divergente')
    gh(folder,'pr','comment',str(pr),'--body',f'[GOJOB RUNNER REVIEW] HEAD {head}; BASE {base}; technical=ACCEPT; strategic=PASS. Comentário, não GitHub APPROVE.')
    wait_ci(folder,pr,head,base)
    gh(folder,'pr','merge',str(pr),'--merge')
    merged=json.loads(gh(folder,'pr','view',str(pr),'--json','state,mergeCommit'))
    if merged['state']!='MERGED' or not merged.get('mergeCommit'):raise RuntimeError('Merge do reparo incerto; não repetir sem reconciliação')
    gh(folder,'issue','close',str(issue),'--reason','completed')
    return {'issue':issue,'pr':pr,'head':head,'merge':merged['mergeCommit']}

def maybe_evolve(repo,home):
    """On a successful cycle, propose evidence-backed opportunities at most daily.

    This is a separate best-effort action. Its failure must never erase a completed
    development task or trigger self-repair of an otherwise healthy runner.
    """
    repo=Path(repo).resolve();metrics=repo/'docs/METRICS_SNAPSHOT.json'
    if not metrics.is_file():return {'status':'NO_METRICS'}
    checkout,sha=installed_runner(repo,home)
    env=os.environ.copy();env['PYTHONPATH']=str(checkout/'automation')
    repo_key=hashlib.sha256(str(repo).encode()).hexdigest()[:16]
    cmd=[sys.executable,'-B','-m','gojob_runner.evolution','--repo',str(repo),
         '--state-root',str(Path(home)/'state'/repo_key),'--submit']
    result=run(cmd,cwd=checkout,env=env,timeout=1800,check=False)
    if result.returncode:
        raise RuntimeError('Evolução não publicada: '+redact(result.stderr or result.stdout)[-500:])
    return json.loads(result.stdout)

def notify(message):
    """Optional Telegram reuse via environment; safe default is no outgoing notification."""
    token=os.environ.get('GOJOB_RUNNER_TELEGRAM_BOT_TOKEN')
    chat=os.environ.get('GOJOB_RUNNER_TELEGRAM_CHAT_ID')
    if not token or not chat:return False
    import urllib.request,urllib.parse
    payload=urllib.parse.urlencode({'chat_id':chat,'text':redact(message)[:2000]}).encode()
    try:
        url='https://api.telegram.org/bot'+token+'/sendMessage'
        urllib.request.urlopen(urllib.request.Request(url,payload),timeout=12).close()
        return True
    except Exception:return False

def main(argv=None):
    p=argparse.ArgumentParser(prog='gojob-runner-supervisor')
    p.add_argument('--repo',required=True)
    p.add_argument('--tasks',default='docs/BACKLOG.json')
    p.add_argument('--home',default=str(Path.home()/'.local/share/gojob-runner'))
    p.add_argument('--task-id')
    p.add_argument('--max-repairs',type=int,default=1)
    p.add_argument('--once',action='store_true',help='execute one monitoring cycle')
    args=p.parse_args(argv)
    if not 0<=args.max_repairs<=2:raise ValueError('max-repairs 0..2')
    home=Path(args.home).expanduser().resolve();home.mkdir(parents=True,mode=0o700,exist_ok=True)
    with (home/'supervisor.lock').open('a') as lock:
        try:fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return 75
        attempts=0
        for cycle in range(args.max_repairs+2):
            try:
                result,source=resume_runner(args.repo,args.tasks,home,args.task_id)
                if result.returncode==0:
                    print(result.stdout[-2200:])
                    try:
                        evolution=maybe_evolve(args.repo,home)
                        if evolution.get('status') not in ('NO_METRICS','COOLDOWN'):
                            print('[EVOLUTION] '+json.dumps(evolution,ensure_ascii=False)[:900])
                    except Exception as evolution_error:
                        # A metrics/agent/GitHub issue is not a runner crash.
                        location,info=incident(home,args.repo,str(evolution_error),'evolution_external')
                        print('[EVOLUTION_BLOCKED] '+info['error'][-350:],file=sys.stderr)
                    return 0
                error=redact(result.stderr or result.stdout)
                if result.returncode in (2,76):
                    print('Bloqueio de política/ambiente; não é defeito a corrigir: '+error[-350:],file=sys.stderr)
                    notify('GoJob runner bloqueado: '+error)
                    return result.returncode
                if result.returncode==75 and cycle<2:
                    time.sleep(5*(cycle+1));continue
                if result.returncode==75:return 75
                if result.returncode!=70:
                    print('Código de falha desconhecido; não corrigir automaticamente: '+error[-350:],file=sys.stderr)
                    return 3
            except Exception as e:error=redact(type(e).__name__+': '+str(e))
            location,info=incident(home,args.repo,error,'runner_crash')
            print('Incidente de runner: '+info['id']+' | '+info['error'][-280:],file=sys.stderr)
            if attempts>=args.max_repairs:break
            if (location/'repair.failed').exists():break
            attempts+=1
            try:
                outcome=repair(args.repo,home,info['id'],info)
                atomic_json(location/'repair.json',outcome)
                continue
            except Exception as e:
                (location/'repair.failed').write_text(redact(e))
                break
        notify('GoJob runner incidente não recuperado; pacote local em '+str(home/'incidents'))
        print('Reparo não concluído. Estado preservado para inspeção.',file=sys.stderr)
        return 3

if __name__=='__main__':sys.exit(main())
