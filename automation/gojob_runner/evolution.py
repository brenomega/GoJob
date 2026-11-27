"""Evidence-backed product evolution proposals; never edits product policy or code."""
from __future__ import annotations
import argparse,json,time,hashlib
from pathlib import Path
from .codex import Codex
from .tools import GitHub,PolicyFailure
from .state import load,save_atomic

SCHEMA=Path(__file__).resolve().parent/'schemas/evolution.json'

def evolve(repo,state_root=None,submit=False,codex=None,scm=None):
    repo=Path(repo).resolve()
    repo_key=hashlib.sha256(str(repo).encode()).hexdigest()[:16]
    root=Path(state_root or Path.home()/'.local/share/gojob-runner/state'/repo_key).expanduser()
    root.mkdir(parents=True,exist_ok=True)
    checkpoint=root/'evolution.json'
    existing=load(checkpoint,{'last_scan':0,'issued':[]})
    if time.time()-existing.get('last_scan',0)<86400:
        return {'status':'COOLDOWN','proposals':[]}
    metrics=repo/'docs/METRICS_SNAPSHOT.json'
    if not metrics.is_file():
        return {'status':'INSUFFICIENT_EVIDENCE','proposals':[]}
    data=json.loads(metrics.read_text())
    if not data.get('source') or not data.get('measured_at'):
        raise PolicyFailure('Métricas sem proveniência — não inventar evolução')
    charter=(repo/'docs/PRODUCT_NORTH_STAR.md').read_text(encoding='utf-8')
    decisions=(repo/'docs/DECISION_LEDGER.md').read_text(encoding='utf-8')
    agent=codex or Codex()
    payload=(f'GoJob: propor no máximo 3 melhorias verificáveis com base em dados reais. '
        'Não introduzir IA no produto por conveniência, não gerar currículos, não comprometer consentimento. '
        'Procure por soluções externas only if tools permitted; sem evidências retorne lista vazia. '
        'Mantenha decisões do usuário até aprovação explícita. '
        f'CONSTITUIÇÃO:\n{charter}\nDECISÕES:\n{decisions}\nMÉTRICAS:\n{json.dumps(data,ensure_ascii=False)}')
    first=agent.run('product-planner',payload,repo,SCHEMA,write=False)
    second=agent.run('independent-strategic-critic',
        'Julgue custo de oportunidade, causalidade e veracidade de cada proposta. Rejeite genéricas, sem métrica, '
        'ou que violem decisão do usuário. Retorne SOMENTE as propostas aprovadas da lista, sem inventar outras.\n'+
        json.dumps(first,ensure_ascii=False),repo,SCHEMA,write=False)
    candidates=[]
    for proposal in second.get('proposals',[])[:3]:
        if proposal not in first.get('proposals',[]):continue
        if not proposal.get('evidence') or not proposal.get('acceptance'):continue
        candidates.append(proposal)
    out=[]
    gh=scm or GitHub(repo)
    for proposal in candidates:
        title='[Evolução] '+proposal['title'][:100]
        if submit:
            issue=gh.get_issue(title)
            if issue:out.append({'title':title,'issue':issue['number'],'existing':True});continue
            body=('## Hipótese\n'+proposal['goal']+'\n\n## Evidência\n'+'\n'.join('- '+x for x in proposal['evidence'])+
                '\n\n## Custo\n'+proposal['cost']+'\n\n## Risco\n'+proposal['risk']+
                '\n\n## Aceite\n'+'\n'.join('- '+x for x in proposal['acceptance'])+
                '\n\nA proposta não altera política confirmada nem entra na fila automaticamente. Revisão de planejamento obrigatória.')
            url=gh.gh('issue','create','--title',title,'--body',body)
            out.append({'title':title,'url':url})
        else:out.append({'title':title,'proposal':proposal})
    if submit:
        existing['last_scan']=time.time();existing['issued']+=out
        save_atomic(checkpoint,existing)
    return {'status':'ISSUES_RECONCILED' if submit else 'PREVIEW_ONLY','proposals':out}

def main(argv=None):
    p=argparse.ArgumentParser(prog='gojob-runner-evolution')
    p.add_argument('--repo',required=True);p.add_argument('--state-root');p.add_argument('--submit',action='store_true')
    a=p.parse_args(argv)
    print(json.dumps(evolve(a.repo,a.state_root,a.submit),indent=2,ensure_ascii=False))

if __name__=='__main__':main()
