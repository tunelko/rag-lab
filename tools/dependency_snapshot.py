"""Publish exact declared pins to GitHub when its automatic scanner misses profiles.

This is a declarative requirements snapshot, not a resolver or vulnerability scan.
It includes every profile and recursive -r include, without suppressing packages.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from urllib.parse import quote

ROOT=Path(__file__).resolve().parents[1]
PIN=re.compile(r'([A-Za-z0-9_.-]+)==([A-Za-z0-9.!+_-]+)')


def pins(path, root, visiting=()):
    path=path.resolve();root=root.resolve()
    if not path.is_relative_to(root):raise ValueError('Requirements include escapes the repository.')
    if path in visiting:raise ValueError('Requirements include cycle.')
    result={}
    for raw in path.read_text('utf-8-sig').splitlines():
        line=raw.split('#',1)[0].strip()
        if not line:continue
        if line.startswith('-r '):
            nested=pins(path.parent/line[3:].strip(),root,(*visiting,path))
        elif line.startswith(('--index-url ','--extra-index-url ')):
            continue
        else:
            match=PIN.fullmatch(line)
            if not match:raise ValueError(f'Unpinned/unsupported requirement in {path.name}: {line}')
            name=re.sub(r'[-_.]+','-',match[1]).lower()
            nested={name:match[2]}
        for name,version in nested.items():
            if name in result and result[name]!=version:raise ValueError('Conflicting pins for '+name)
            result[name]=version
    return result


def snapshot(root, sha, job_id):
    if not re.fullmatch(r'[a-fA-F0-9]{40,64}',sha):raise ValueError('Invalid commit SHA.')
    manifests={}
    for path in sorted((root/'requirements').glob('*.txt')):
        relative=path.relative_to(root).as_posix()
        resolved={name:{'package_url':f'pkg:pypi/{name}@{quote(version,safe=".")}',
                        'relationship':'direct','scope':'development' if path.name=='dev.txt' else 'runtime'}
                  for name,version in pins(path,root).items()}
        manifests[relative]={'name':relative,'file':{'source_location':relative},'resolved':resolved}
    if not manifests:raise ValueError('No requirements profiles found.')
    return {'version':0,'sha':sha,'ref':'refs/heads/main',
            'job':{'id':str(job_id),'correlator':'rag-local-pinned-requirements'},
            'detector':{'name':'rag-local-pinned-requirements','version':'1.0.0',
                        'url':'https://github.com/tunelko/rag-lab/blob/main/tools/dependency_snapshot.py'},
            'scanned':datetime.now(timezone.utc).isoformat(),
            'metadata':{'method':'declared-exact-pins','transitive_resolution':False},
            'manifests':manifests}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sha',required=True)
    parser.add_argument('--job-id',required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    result=snapshot(ROOT,args.sha,args.job_id)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(f"Prepared {len(result['manifests'])} declared requirements profiles.")


if __name__=='__main__':main()
