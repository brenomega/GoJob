"""Git/GitHub boundary. Every destructive/external operation is explicit and observed."""
from __future__ import annotations
import json, os, re, subprocess, sys, time
from pathlib import Path

class PolicyFailure(RuntimeError): pass
class BaseAdvanced(PolicyFailure): pass
class MergeProtectionRequired(PolicyFailure): pass
class ExternalFailure(RuntimeError): pass
class TransientFailure(ExternalFailure): pass

SAFE_TRANSIENT = ('rate limit','429','503','timed out','connection reset','temporarily unavailable','could not resolve')

def command(argv, cwd=None, timeout=180, env=None):
    if not isinstance(argv, (list,tuple)) or not argv or any(not isinstance(i,str) for i in argv):
        raise ValueError('Command must be a typed argv list')
    try:
        result=subprocess.run(argv, cwd=cwd, env=env, timeout=timeout, capture_output=True, text=True)
    except FileNotFoundError as e:
        raise ExternalFailure(f'{argv[0]} não instalado') from e
    except subprocess.TimeoutExpired as e:
        raise TransientFailure(f'timeout de {argv[0]}, efeito externo inconclusivo') from e
    if result.returncode:
        diagnostic=(result.stderr or result.stdout)[-1400:]
        if any(s in diagnostic.lower() for s in SAFE_TRANSIENT):
            raise TransientFailure(f'{argv[0]} indisponível: {diagnostic}')
        raise ExternalFailure(f'{argv[0]} exit={result.returncode}: {diagnostic}')
    return result.stdout.strip()

def clean_env():
    env=os.environ.copy()
    for key in ('OPENAI_API_KEY','CODEX_API_KEY','GH_TOKEN','GITHUB_TOKEN','GOJOB_VAULT_KEY',
                'TELEGRAM_BOT_TOKEN','GOJOB_RUNNER_TELEGRAM_BOT_TOKEN'):
        env.pop(key,None)
    env['PYTHONDONTWRITEBYTECODE']='1'
    return env

def git(repo,*args,timeout=180):
    return command(['git',*args],cwd=repo,timeout=timeout)

def assert_clean(repo):
    dirty=git(repo,'status','--porcelain')
    if dirty:
        raise PolicyFailure('Working tree suja; preservar, não reset/stash/clean: '+dirty[:500])

def assert_base_ancestor(repo,base,head):
    observed=git(repo,'merge-base',base,head)
    if observed!=base:
        raise PolicyFailure(f'BASE {base[:12]} não é ancestral do HEAD {head[:12]}')
    return True

def commit_audit(repo,base,task_id,max_commits=8,max_lines=600):
    """Tests real commits, never trusts developer declarations or counts."""
    head=git(repo,'rev-parse','HEAD')
    assert_base_ancestor(repo,base,head)
    commits=git(repo,'rev-list','--reverse',f'{base}..{head}').splitlines()
    if not 1 <= len(commits) <= max_commits:
        raise PolicyFailure('Quantidade de commits fora do intervalo [1,8]')
    pattern=re.compile(r'^(feat|fix|refactor|test|docs|chore|perf|ci)\([a-z0-9_-]+\): .{8,160} \[#'+re.escape(task_id)+r'\]$')
    for commit in commits:
        parents=git(repo,'rev-list','--parents','-n','1',commit).split()
        if len(parents) not in (2,3):
            raise PolicyFailure('Histórico de commits anômalo na branch')
        subject=git(repo,'show','-s','--format=%s',commit)
        if len(parents)==3:
            if subject!=f'chore(sync): integre develop à tarefa [#{task_id}]':
                raise PolicyFailure('Merge commit não autorizado na tarefa')
            if git(repo,'merge-base',parents[2],base)!=parents[2]:
                raise PolicyFailure('Merge commit importa conteúdo de branch não confiável')
            continue
        if not pattern.fullmatch(subject):
            raise PolicyFailure('Assunto inválido: '+subject)
        out=git(repo,'show','--numstat','--format=',commit)
        total=0
        for line in out.splitlines():
            fields=line.split('\t')
            if len(fields)<3:continue
            total+=sum(int(v) for v in fields[:2] if v.isdecimal())
            pathname=fields[2].lower()
            if any(x in pathname for x in ('.env','credentials','secrets','auth.json','profile.enc','gojob.db','.pem','.key','cookies','session.json')):
                raise PolicyFailure('Artefato potencialmente secreto no commit')
        if total>max_lines:
            raise PolicyFailure(f'Commit {commit[:12]} excede {max_lines} linhas alteradas')
    return {'head':head,'commits':commits}


def is_product_test_path(path):
    """Classify test/fixture files conservatively for agent write scoping."""
    name=Path(path).name
    return (path.startswith(('tests/','test/','src/test/','__tests__/'))
            or '/__tests__/' in path or '/test/' in path or '/tests/' in path
            or name.startswith('test_') and name.endswith('.py')
            or name.endswith(('.test.ts','.spec.ts','.test.js','.spec.js',
                              '.test.tsx','.spec.tsx','.test.jsx','.spec.jsx','_test.go')))

def product_code_changes(repo,base_sha):
    """Read-only detection; runner never manufactures application tests."""
    changed=git(repo,'diff','--name-only',f'{base_sha}..HEAD').splitlines()
    extensions=('.py','.ts','.tsx','.js','.jsx','.java','.go','.rs','.kt','.cs')
    return [path for path in changed if (path.endswith(extensions)
            and not path.startswith(('automation/','docs/'))
            and not is_product_test_path(path))]

def verify_product(repo, task, base_sha, timeout=600):
    """Run immutable baseline gates AND actual product tests when product code exists.

    The seed's unittest checks are runner/scaffold checks, not evidence of a
    scraper, browser integration, or any other application implementation.
    """
    assert_clean(repo)
    cfg=json.loads(git(repo,'show',f'{base_sha}:config/quality_gates.json'))
    scripts=cfg.get('commands',[])
    if not scripts:raise PolicyFailure('Quality gates vazios; não certificar testes')
    actual=[]
    for argv in scripts:
        if not isinstance(argv,list) or not argv or any(not isinstance(t,str) or not t for t in argv):
            raise PolicyFailure('Gate inválido na constituição da base')
        if argv[0] not in ('python','python3','pytest','npm','mvn','bash','node'):
            raise PolicyFailure('Gate usa executável não aprovado')
        resolved=[sys.executable if x in ('python','python3') else x for x in argv]
        env=clean_env();env['PYTHONPATH']=str(Path(repo)/'automation')+os.pathsep+str(Path(repo)/'automation'/'supervisor')+os.pathsep+str(repo)+os.pathsep+env.get('PYTHONPATH','')
        command(resolved,cwd=repo,timeout=timeout,env=env)
        actual.append({'cmd':resolved,'kind':'baseline'})
    source=product_code_changes(repo,base_sha)
    if source and task.get('type')!='runner_fix':
        root=Path(repo)
        test_files=git(repo,'ls-files').splitlines()
        product_tests=[t for t in test_files if (t.startswith(('tests/','test/','src/test/','__tests__/'))
            and t!='tests/test_bootstrap.py' and t.endswith(('.py','.ts','.tsx','.js','.jsx','.java','.go','.cs','.rs','.kt')))
            or is_product_test_path(t)]
        if not product_tests:
            raise PolicyFailure('Código de produto sem testes de produto: scaffold do runner não é evidência')
        env=clean_env();env['PYTHONPATH']=str(root)+os.pathsep+env.get('PYTHONPATH','')
        # Polyglot product: do not silently skip TypeScript extension tests
        # because a Python backend happens to have a pyproject.toml.
        toolchains=[]
        if (root/'pyproject.toml').is_file():
            toolchains.append([sys.executable,'-m','pytest','-q'])
        if (root/'package.json').is_file():
            package=json.loads((root/'package.json').read_text(encoding='utf-8'))
            script=package.get('scripts',{}).get('test','').strip()
            if not script or script in ('echo ok','echo "ok"','exit 0') or 'no test specified' in script:
                raise PolicyFailure('package.json não define teste real para a extensão/front-end')
            toolchains.append(['npm','test'])
        if (root/'pom.xml').is_file():toolchains.append(['mvn','--batch-mode','verify'])
        if (root/'go.mod').is_file():toolchains.append(['go','test','./...'])
        if (root/'Cargo.toml').is_file():toolchains.append(['cargo','test','--workspace'])
        if not toolchains:
            raise PolicyFailure('Código de produto sem ferramenta de testes reconhecida; agente deve preparar pipeline antes de certificar')
        for cmd in toolchains:
            command(cmd,cwd=repo,timeout=max(timeout,900),env=env)
            actual.append({'cmd':cmd,'kind':'product'})
    return {'commands':actual,'base_sha':base_sha,'product_sources':source}

class GitHub:
    """GitHub is used by the controller; agent roles have no GitHub mutation credential."""
    def __init__(self,repo,owner='brenomega/GoJob'):
        self.repo=Path(repo).resolve();self.owner=owner
    def gh(self,*argv,timeout=180):return command(['gh',*argv],cwd=self.repo,timeout=timeout)
    def verify_identity(self):
        git(self.repo,'rev-parse','--is-inside-work-tree')
        remote=git(self.repo,'remote','get-url','origin')
        acceptable={f'https://github.com/{self.owner}.git',f'https://github.com/{self.owner}',
                    f'git@github.com:{self.owner}.git',f'git@github.com:{self.owner}',
                    f'ssh://git@github.com/{self.owner}.git'}
        if remote not in acceptable:raise PolicyFailure('Origin deve ser '+self.owner)
        self.gh('auth','status')
        identity=self.gh('api','user','--jq','.login').strip()
        if identity.lower()!='brenomega':
            raise PolicyFailure('Identidade GitHub inesperada: runner exige brenomega')
        return True
    def preflight(self):
        self.verify_identity()
        assert_clean(self.repo)
        return True
    def get_issue(self,title):
        rows=json.loads(self.gh('issue','list','--state','all','--limit','200','--json','number,title,state'))
        found=[x for x in rows if x['title']==title]
        if len(found)>1:raise PolicyFailure('Issue duplicada com o mesmo título')
        return found[0] if found else None
    def issue(self,task):
        title=f"[{task['id']}] {task['title']}"
        found=self.get_issue(title)
        if found:
            if found['state']!='OPEN':raise PolicyFailure('Issue já encerrada; conciliar antes de continuar')
            return int(found['number'])
        body='## Objetivo\n'+task['goal']+'\n\n## Critérios\n'+'\n'.join('- '+x for x in task['acceptance'])+'\n\n## Dependências\n'+str(task['dependencies'])
        url=self.gh('issue','create','--title',title,'--body',body)
        number=re.search(r'/issues/(\d+)',url)
        if not number:raise TransientFailure('Issue criada? resposta sem número; reconciliar antes de repetir')
        return int(number.group(1))
    def issue_close(self,number):
        state=json.loads(self.gh('issue','view',str(number),'--json','state'))['state']
        if state=='CLOSED':return True
        if state!='OPEN':raise PolicyFailure('Issue em estado inválido')
        self.gh('issue','close',str(number),'--reason','completed')
        if json.loads(self.gh('issue','view',str(number),'--json','state'))['state']!='CLOSED':
            raise TransientFailure('Fechamento da issue não confirmado')
        return True
    def get_pr(self,branch):
        rows=json.loads(self.gh('pr','list','--state','all','--limit','200','--json','number,headRefName,state'))
        found=[x for x in rows if x['headRefName']==branch]
        if len(found)>1:raise PolicyFailure('Múltiplos PRs para mesma branch')
        return found[0] if found else None
    def pr(self,number):
        return json.loads(self.gh('pr','view',str(number),'--json','number,state,isDraft,headRefOid,baseRefOid,headRefName,baseRefName,mergeCommit'))
    def ensure_pr(self,task,branch,issue):
        existing=self.get_pr(branch)
        if existing:
            if existing['state']!='OPEN':raise PolicyFailure('PR anterior fechado; recuperação explícita')
            return int(existing['number'])
        body=f"Refs #{issue}\n\n## EAP {task['id']}\n{task['goal']}\n\n## Aceite\n"+'\n'.join('- '+x for x in task['acceptance'])+'\n\nSem aprovação nativa do próprio autor. Revisão técnica independente certificada pelo harness.\n'
        out=self.gh('pr','create','--base','develop','--head',branch,'--title',f"[{task['id']}] {task['title']}",'--body',body)
        m=re.search(r'/pull/(\d+)',out)
        if not m:raise TransientFailure('PR talvez criado; reconciliar antes de repetir')
        return int(m.group(1))
    def snapshot(self,pr,branch,head,base_sha,attempts=10,sleep=3):
        """Bounded propagation fence, catching old head and real base advance."""
        last=None
        for i in range(attempts):
            info=self.pr(pr);last=info
            identity=(info.get('number')==pr and info.get('headRefName')==branch and info.get('baseRefName')=='develop')
            if not identity or info.get('state')!='OPEN' or info.get('isDraft'):
                raise PolicyFailure('Identidade PR mudou: '+str(info)[:600])
            remote_head=git(self.repo,'ls-remote','origin','refs/heads/'+branch).split()
            if info.get('baseRefOid') != base_sha:
                raise BaseAdvanced('Base develop avançou: sincronizar com merge commit e revalidar')
            if info.get('headRefOid')==head and remote_head and remote_head[0]==head:return info
            if i+1<attempts:time.sleep(sleep)
        raise TransientFailure(f'Propagação GitHub não confirmou HEAD {head[:12]}: {last}')
    def comment(self,pr,head,base,kind,report):
        # Public repository: never publish LLM-authored prose or diagnostics. These
        # remain in the private controller state for independent audit.
        decision=report.get('decision','UNKNOWN')
        if decision not in ('PASS','REVISE','STOP','ACCEPT','BLOCK'):
            raise PolicyFailure('Review decision inválida para comentário público')
        finding_count=len(report.get('findings',[]))
        body=(f'[GOJOB {kind}]\nHEAD `{head}`; BASE `{base}`\n'
              f'Decision: {decision}; findings: {finding_count}. '
              'Parecer completo preservado no journal privado do controlador; '
              'isto não é GitHub APPROVE.')
        self.gh('pr','comment',str(pr),'--body',body)
    def _api(self,path):
        return json.loads(self.gh('api',f'repos/{self.owner}/{path}',timeout=100))

    def checks(self,pr,head,base,branch,timeout=900):
        from .merge_evidence import verify_ci

        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            self.snapshot(pr,branch,head,base,attempts=1)
            try:
                proof=verify_ci(self._api,pr,head,base)
            except ValueError as exc:
                raise PolicyFailure(str(exc)) from exc
            if proof:
                return proof
            time.sleep(15)
        raise TransientFailure('CI verify não confirmado para PR, HEAD e BASE exatos; preservar PR')

    def confirm_merge(self,pr_info,branch,head,base):
        from .merge_evidence import reconcile_merge

        try:
            return reconcile_merge(lambda *args: git(self.repo,*args),pr_info,
                                   head=head,base=base,branch=branch)
        except ValueError as exc:
            raise PolicyFailure(str(exc)) from exc

    def merge(self,pr,branch,head,base):
        from .merge_evidence import require_strict_protection

        proof=self.checks(pr,head,base,branch,timeout=60)
        try:
            require_strict_protection(self._api,proof)
        except TransientFailure:
            raise
        except (ValueError,ExternalFailure) as exc:
            raise MergeProtectionRequired('Confirme a proteção estrita de develop com verify do GitHub Actions, inclusive administradores') from exc
        self.snapshot(pr,branch,head,base,attempts=1)
        self.gh('pr','merge',str(pr),'--merge','--match-head-commit',head)
        item=self.pr(pr)
        if item.get('state')!='MERGED' or not item.get('mergeCommit'):
            raise TransientFailure('Resultado do merge incerto: reconciliar antes de repetir')
        self.confirm_merge(item,branch,head,base)
        return item

def worktree_changes(repo):
    """Exact paths, including untracked and deletions; staged changes are not agent-owned."""
    staged=[x for x in git(repo,'diff','--cached','--no-renames','--name-only','-z').split('\x00') if x]
    modified=[x for x in git(repo,'diff','--no-renames','--name-only','-z').split('\x00') if x]
    untracked=[x for x in git(repo,'ls-files','--others','--exclude-standard','-z').split('\x00') if x]
    return staged,sorted(set(modified+untracked))

def validate_path(repo,path):
    from pathlib import PurePosixPath
    if not isinstance(path,str) or not path or '\x00' in path or '\n' in path or '\\' in path:
        raise PolicyFailure('Caminho de commit inválido')
    candidate=PurePosixPath(path)
    if candidate.is_absolute() or '..' in candidate.parts or '.git' in candidate.parts or path!=str(candidate):
        raise PolicyFailure('Caminho de commit fora de escopo')
    full=(Path(repo)/path).resolve()
    if not full.is_relative_to(Path(repo).resolve()):
        raise PolicyFailure('Caminho ultrapassa worktree/symlink externo')
    lowered=path.lower()
    if any(x in lowered for x in ('.env','credentials','secrets','auth.json','profile.enc','gojob.db','.pem','.key','cookies')):
        raise PolicyFailure('Caminho sensível no plano de commit')
    return path

def apply_commit_plan(repo,task_id,report,allowed_prefixes=None,max_commits=8,allowed_files=()):
    """Controller executes structured commit *intent*, not unrestricted agent Git writes.

    This is necessary because Codex workspace-write may mount the Git dir read-only.
    No shell commands or arbitrary path globs from the model are executed.
    """
    if report.get('status')!='DONE':raise PolicyFailure('Agente não concluiu')
    staged,dirty=worktree_changes(repo)
    if staged:raise PolicyFailure('Arquivos já staged antes de aplicar plano; estado inconclusivo')
    plan=report.get('commit_plan')
    if not isinstance(plan,list) or not 1<=len(plan)<=max_commits:
        raise PolicyFailure('Plano de commits ausente/inválido')
    all_paths=[]
    pattern=re.compile(r'^(feat|fix|refactor|test|docs|chore|perf|ci)\([a-z0-9_-]+\): .{8,160} \[#'+re.escape(task_id)+r'\]$')
    for piece in plan:
        if not isinstance(piece,dict) or not pattern.fullmatch(piece.get('message','')):
            raise PolicyFailure('Mensagem de commit não atende formato EAP')
        if not isinstance(piece.get('paths'),list) or not piece['paths']:
            raise PolicyFailure('Commit sem arquivos explícitos')
        for path in piece['paths']:
            validate_path(repo,path)
            if allowed_prefixes is not None and not path.startswith(allowed_prefixes) and path not in allowed_files:
                raise PolicyFailure('Reparo tentaria incluir arquivo fora do runner')
            all_paths.append(path)
    if len(set(all_paths))!=len(all_paths) or set(all_paths)!=set(dirty):
        raise PolicyFailure('Plano não corresponde exatamente às alterações existentes')
    results=[]
    for piece in plan:
        # Input paths already validated and exact; no shell, wildcard, reset, or amend.
        if worktree_changes(repo)[0]:
            raise PolicyFailure('Índice staged antes de preparar o próximo commit; preservar estado')
        git(repo,'--literal-pathspecs','add','--',*piece['paths'])
        try:git(repo,'diff','--cached','--check')
        except ExternalFailure as e:raise PolicyFailure('Whitespace/error no diff staged') from e
        staged_now,_=worktree_changes(repo)
        if set(staged_now)!=set(piece['paths']):
            raise PolicyFailure('Índice staged difere do plano; preservar estado para diagnóstico')
        git(repo,'commit','-m',piece['message'])
        # Per-commit smoke for Python syntax without creating a new test suite.
        for path in piece['paths']:
            if path.endswith('.py') and (Path(repo)/path).is_file():
                try:compile((Path(repo)/path).read_bytes(),path,'exec')
                except SyntaxError as e:raise PolicyFailure('Commit contém sintaxe Python inválida: '+path) from e
        results.append(git(repo,'rev-parse','HEAD'))
    assert_clean(repo)
    return results
