"""GoJob release policy: vA.B.C with user-defined change impact classes."""
import re

def bump(version:str,impact:str)->str:
    m=re.fullmatch(r'(\d+)\.(\d+)\.(\d+)',version)
    if not m:raise ValueError('Versão inválida')
    a,b,c=map(int,m.groups())
    if impact=='bug':c+=1
    elif impact=='procedural':b+=1;c=0
    elif impact=='feature':a+=1;b=c=0
    else:raise ValueError('Impacto inválido')
    return f'{a}.{b}.{c}'
