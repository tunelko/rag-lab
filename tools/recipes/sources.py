"""Explicit public-source registry, pinned downloads and preserved originals."""
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import time
import uuid

import httpx


@dataclass(frozen=True)
class Source:
    id: str
    kind: str
    repository: str
    filename: str = ''
    collection: str = ''
    license_hint: str = ''


SOURCES = (
    Source('huggingface_es', 'hf', 'belenarbizu/recetas-espanolas', 'recetas.json', license_hint='CC-BY-4.0 (prompt; not verified)'),
    Source('open_recipe_archive', 'github', 'AdamBouhmad/open-recipe-archive', collection='collections/cocina-espanola'),
    Source('somosnlp', 'hf', 'somosnlp/recetas-cocina', 'dataset.csv'),
    Source('recetas_abuela', 'hf', 'somosnlp/RecetasDeLaAbuela', 'main.csv'),
    Source('peluso', 'github', 'peluso-cocina/recetario-culinario', collection='Recetas'),
    Source('pdf_regionales', 'pdf', 'https://www.esmadrid.com/sites/default/files/documentos/recetario_rcm_22_10_2015.pdf', 'recetario_madrid.pdf'),
)


def stamp():
    return datetime.now(timezone.utc).isoformat()


def write_json(path, value):
    write_bytes(path, (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode())


def write_bytes(path, value):
    path = Path(path)
    if path.is_file() and path.read_bytes() == value:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.pending')
    temporary.write_bytes(value)
    temporary.replace(path)
    return True


def digest(path):
    with Path(path).open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def stored_path(output, value):
    parts=str(value).replace('\\','/').split('/')
    if 'raw' not in parts:raise ValueError('Original artifact is outside raw/.')
    path=output.joinpath(*parts[parts.index('raw'):]).resolve()
    if not path.is_relative_to(output.resolve()):raise ValueError('Original artifact escapes the corpus directory.')
    return path


def validate_artifacts(output, info):
    for item in info.get('artifacts',[]):
        path=stored_path(output,item['path'])
        if not path.is_file() or digest(path)!=item['sha256']:
            raise ValueError('Original source checksum mismatch; raw was not overwritten: '+path.name)
    return len(info.get('artifacts',[]))


def client():
    return httpx.Client(follow_redirects=True, trust_env=False, timeout=httpx.Timeout(90, connect=20),
                        headers={'User-Agent': 'MusaRecipeCorpus/1.0 (public datasets; no website scraping)'})


def discover(source, output, session, force=False):
    location = output / 'cache' / (source.id + '-discovery.json')
    if location.is_file() and not force:
        return json.loads(location.read_text(encoding='utf-8'))
    result = {'id': source.id, 'kind': source.kind, 'repository': source.repository, 'checked_at': stamp(),
              'license_hint': source.license_hint, 'status': 'unavailable'}
    try:
        if source.kind == 'hf':
            response = session.get('https://huggingface.co/api/datasets/' + source.repository)
            response.raise_for_status()
            info = response.json()
            files = [item['rfilename'] for item in info.get('siblings', [])]
            if source.filename not in files:
                raise ValueError('Expected file is absent: ' + source.filename)
            result.update(revision=info['sha'], files=files, license_declared=(info.get('cardData') or {}).get('license'),
                          homepage='https://huggingface.co/datasets/' + source.repository)
        elif source.kind == 'github':
            base = 'https://api.github.com/repos/' + source.repository
            response = session.get(base);response.raise_for_status();info = response.json()
            commit = session.get(base + '/commits/' + info['default_branch']);commit.raise_for_status()
            license_info = session.get(base + '/license')
            license_data = license_info.json() if license_info.status_code == 200 else {}
            result.update(revision=commit.json()['sha'], branch=info['default_branch'],
                          license_declared=(info.get('license') or {}).get('spdx_id'),
                          license_file=license_data.get('path'), homepage='https://github.com/' + source.repository)
        else:
            response = session.head(source.repository);response.raise_for_status()
            result.update(revision=None, homepage=source.repository, license_declared=None)
        result['status'] = 'available'
    except (httpx.HTTPError, ValueError, KeyError) as error:
        result['error'] = str(error)
        if isinstance(error, httpx.HTTPStatusError):
            result['http_status'] = error.response.status_code
    write_json(location, result)
    return result


def _http_file(session, url, destination, *, limit=200 * 1024**2):
    if destination.is_file():
        return {'path': str(destination), 'sha256': digest(destination), 'bytes': destination.stat().st_size, 'url': url}
    destination.parent.mkdir(parents=True, exist_ok=True)
    pending = destination.with_name(destination.name + '.' + uuid.uuid4().hex + '.pending')
    for attempt in range(3):
        try:
            with session.stream('GET', url) as response:
                response.raise_for_status()
                with pending.open('wb') as target:
                    size = 0
                    for block in response.iter_bytes(1024 * 1024):
                        size += len(block)
                        if size > limit:
                            raise ValueError('Download exceeds the source size limit.')
                        target.write(block)
            pending.replace(destination)
            break
        except (httpx.TransportError, httpx.HTTPStatusError) as error:
            retryable=not isinstance(error,httpx.HTTPStatusError) or error.response.status_code in {408,429,500,502,503,504}
            if attempt==2 or not retryable:raise
            time.sleep(2**attempt)
        finally:
            if pending.is_file():pending.unlink()
    return {'path': str(destination), 'sha256': digest(destination), 'bytes': size, 'url': url}


def download(source, info, output, session):
    if info['status'] != 'available':
        return {**info, 'download_status': 'unavailable', 'artifacts': []}
    revision = info.get('revision') or 'original'
    root = output / 'raw' / source.id / revision
    ledger = root / 'download.json'
    if ledger.is_file():
        result = json.loads(ledger.read_text(encoding='utf-8'))
        if result.get('download_status') == 'downloaded':
            try:
                validate_artifacts(output,result)
                return {**result,'reused':True}
            except (OSError,ValueError) as error:
                return {**result,'download_status':'error','error':str(error)}
    result = {**info, 'artifacts': [], 'download_status': 'error'}
    try:
        if source.kind == 'hf':
            names = [source.filename] + [name for name in info['files'] if Path(name).name.lower() in {'readme.md', 'license', 'license.md', 'license.txt'}]
            for name in dict.fromkeys(names):
                if not (root/name).resolve().is_relative_to(root.resolve()):raise ValueError('Unsafe dataset artifact path.')
                url = f'https://huggingface.co/datasets/{source.repository}/resolve/{revision}/{name}'
                result['artifacts'].append(_http_file(session, url, root / name))
            result['main_path'] = str(root / source.filename)
        elif source.kind == 'github':
            repository = root / 'repository'
            if not repository.is_dir():
                root.mkdir(parents=True, exist_ok=True)
                pending = root / ('checkout-' + uuid.uuid4().hex)
                commands = [['git', 'clone', '--filter=blob:none', '--no-checkout', '--depth', '1', 'https://github.com/' + source.repository + '.git', str(pending)],
                            ['git', '-C', str(pending), 'sparse-checkout', 'init', '--cone'],
                            ['git', '-C', str(pending), 'sparse-checkout', 'set', source.collection],
                            ['git', '-C', str(pending), 'fetch', '--depth', '1', 'origin', revision],
                            ['git', '-C', str(pending), 'checkout', revision]]
                for command in commands:
                    completed = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=240)
                    if completed.returncode:
                        raise ValueError(completed.stderr.strip()[-1500:])
                pending.rename(repository)
            for path in sorted(repository.rglob('*')):
                if path.is_file() and '.git' not in path.relative_to(repository).parts:
                    if not path.resolve().is_relative_to(repository.resolve()):raise ValueError('Unsafe repository link.')
                    result['artifacts'].append({'path': str(path), 'sha256': digest(path), 'bytes': path.stat().st_size,
                        'url': f'https://github.com/{source.repository}/blob/{revision}/{path.relative_to(repository).as_posix()}'})
            result['main_path'] = str(repository / source.collection)
        else:
            artifact = _http_file(session, source.repository, root / source.filename)
            result['artifacts'].append(artifact);result['main_path'] = artifact['path']
        result.update(download_status='downloaded', downloaded_at=stamp())
    except (httpx.HTTPError, OSError, ValueError, subprocess.TimeoutExpired) as error:
        result['error'] = str(error)
    write_json(ledger, result)
    return result
