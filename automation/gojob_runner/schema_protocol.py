"""Strict JSON schema contract for Codex structured outputs (stdlib only).

Source schemas are checked before releases. At runtime, a schema with missing
required entries can be normalized in a private TEMP file, without changing
review policy, Git history, or the source artifact. This is a protocol fix,
not an approval of the agent's response.
"""
from __future__ import annotations
import copy
import hashlib
import json
import os
from pathlib import Path


class SchemaProtocolError(ValueError):
    pass


def schema_errors(schema):
    """Find OpenAI strict-structured-output errors in nested object/array nodes."""
    result=[]
    if not isinstance(schema,dict):
        return ['root: schema must be an object']
    def inspect(node,where):
        if not isinstance(node,dict):
            result.append(f'{where}: schema node must be object')
            return
        typ=node.get('type')
        if typ=='object' or 'properties' in node:
            if typ!='object':result.append(f'{where}: properties without type object')
            props=node.get('properties')
            if not isinstance(props,dict):
                result.append(f'{where}: properties must be an object')
            else:
                req=node.get('required')
                if not isinstance(req,list) or len(req)!=len(set(str(x) for x in req)) or set(req)!=set(props):
                    result.append(f'{where}: required must contain every property exactly once: {sorted(props)}')
                for name,child in props.items():inspect(child,f'{where}.properties.{name}')
            if node.get('additionalProperties') is not False:
                result.append(f'{where}: additionalProperties must be false')
        if typ=='array' or 'items' in node:
            if typ!='array':result.append(f'{where}: items without type array')
            if not isinstance(node.get('items'),dict):result.append(f'{where}: array needs schema items')
            else:inspect(node['items'],where+'.items')
        for branch in ('anyOf','oneOf','allOf'):
            if branch in node:
                members=node[branch]
                if not isinstance(members,list):result.append(f'{where}: {branch} must be an array')
                else:
                    for i,child in enumerate(members):inspect(child,f'{where}.{branch}[{i}]')
        for defkey in ('$defs','definitions'):
            if defkey in node:
                if not isinstance(node[defkey],dict):result.append(f'{where}: {defkey} must be an object')
                else:
                    for name,child in node[defkey].items():inspect(child,f'{where}.{defkey}.{name}')
    inspect(schema,'root')
    return result


def normalized_schema(schema):
    """Only repair transport constraints; preserve types, enums and properties."""
    schema=copy.deepcopy(schema)
    def normalize(node):
        if not isinstance(node,dict):return
        if node.get('type')=='object' and isinstance(node.get('properties'),dict):
            node['required']=list(node['properties'])
            node['additionalProperties']=False
            for child in node['properties'].values():normalize(child)
        if node.get('type')=='array' and isinstance(node.get('items'),dict):normalize(node['items'])
        for key in ('anyOf','oneOf','allOf'):
            if isinstance(node.get(key),list):
                for child in node[key]:normalize(child)
        for key in ('$defs','definitions'):
            if isinstance(node.get(key),dict):
                for child in node[key].values():normalize(child)
    normalize(schema)
    errors=schema_errors(schema)
    if errors:raise SchemaProtocolError('; '.join(errors[:8]))
    return schema


def source_schema_errors(path):
    path=Path(path)
    try:schema=json.loads(path.read_text(encoding='utf-8'))
    except (OSError,UnicodeError,json.JSONDecodeError) as e:return [f'{path}: invalid JSON: {e}']
    return [f'{path}: {x}' for x in schema_errors(schema)]


def prepared_schema(path,private_dir):
    """Provide valid CLI schema from a private temporary directory if needed.

    The original repository file stays unchanged; no agent can convert a
    forbidden decision into PASS by changing schema meaning.
    """
    path=Path(path)
    try:source=json.loads(path.read_text(encoding='utf-8'))
    except (OSError,UnicodeError,json.JSONDecodeError) as e:
        raise SchemaProtocolError(f'cannot read Codex schema {path.name}: {e}') from e
    fixed=normalized_schema(source)
    if fixed==source:return path
    private_dir=Path(private_dir)
    private_dir.mkdir(parents=True,exist_ok=True,mode=0o700)
    stamp=hashlib.sha256(json.dumps(source,sort_keys=True).encode()).hexdigest()[:12]
    target=private_dir/('strict-'+stamp+'-'+path.name)
    descriptor=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
    with os.fdopen(descriptor,'w',encoding='utf-8') as f:
        json.dump(fixed,f,ensure_ascii=False,indent=2)
        f.write('\n')
    return target
