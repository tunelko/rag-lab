"""Explicit public CTI downloads, pinned to repository commits; never execute data."""
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import tarfile
import urllib.request
from urllib.parse import urlsplit

REPOSITORIES = {'mitre': ('mitre-attack/attack-stix-data', 'master'),
                'brandefense': ('BRANDEFENSE/threat-actor-dataset', 'main'),
                'misp': ('MISP/misp-galaxy', 'main')}
HOSTS = {'api.github.com', 'raw.githubusercontent.com', 'codeload.github.com'}
LIMIT = 128 * 1024 ** 2


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError('La fuente intentó redirigir la descarga.')


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix(path.suffix + '.pending')
    pending.write_text(json.dumps(data, ensure_ascii=False, indent=2), 'utf-8')
    pending.replace(path)


def download(url, destination, cancelled=lambda: False, progress=lambda **kw: None):
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or parsed.hostname not in HOSTS or parsed.username or parsed.password:
        raise ValueError('Origen de datos no permitido.')
    destination.parent.mkdir(parents=True, exist_ok=True)
    pending = destination.with_suffix(destination.suffix + '.pending')
    client = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    request = urllib.request.Request(url, headers={'User-Agent': 'Musa-CTI/1.0', 'Accept': 'application/vnd.github+json'})
    try:
        with client.open(request, timeout=60) as response, pending.open('wb') as output:
            total = int(response.headers.get('Content-Length', '0'))
            count, reported = 0, -1
            while block := response.read(256 * 1024):
                if cancelled():
                    raise InterruptedError('Descarga cancelada.')
                count += len(block)
                if count > LIMIT:
                    raise ValueError('La descarga supera 128 MiB.')
                output.write(block)
                bucket = count // (1024 * 1024)
                if bucket != reported:
                    progress(bytes=count, total_bytes=total)
                    reported = bucket
        pending.replace(destination)
    finally:
        pending.unlink(missing_ok=True)
    return {'url': url, 'path': str(destination), 'bytes': destination.stat().st_size, 'sha256': digest(destination)}


def snapshot(root: Path, source: str, attack_version: str, cancelled, observer):
    repository, branch = REPOSITORIES[source]
    discovery = root / 'cache' / f'{source}-head.json'
    download(f'https://api.github.com/repos/{repository}/commits/{branch}', discovery, cancelled)
    revision = json.loads(discovery.read_text('utf-8'))['sha']
    if not re.fullmatch('[a-f0-9]{40}', revision):
        raise ValueError('La fuente no devolvió un commit válido.')
    folder = root / 'raw' / source / revision / (attack_version if source == 'mitre' else 'snapshot')
    receipt = folder / 'download.json'
    if receipt.is_file():
        record = json.loads(receipt.read_text('utf-8'))
        if all(Path(item['path']).is_file() and digest(Path(item['path'])) == item['sha256'] for item in record['artifacts']):
            return record
    paths = ([f'enterprise-attack/enterprise-attack-{attack_version}.json', 'LICENSE.txt'] if source == 'mitre' else
             ['data/apt-groups.json', 'data/stix/threat-actors.stix.json', 'LICENSE'] if source == 'brandefense' else [])
    artifacts = []
    for name in paths:
        observer(phase=f'Descargando {source} · {name}')
        artifacts.append(download(f'https://raw.githubusercontent.com/{repository}/{revision}/{name}', folder / name,
                                  cancelled, lambda **counts: observer(phase=f'Descargando {source} · {name}', **counts)))
    if source == 'misp':
        observer(phase='Descargando MISP Galaxy · instantánea completa')
        archive = folder / 'misp-galaxy.tar.gz'
        artifacts.append(download(f'https://codeload.github.com/{repository}/tar.gz/{revision}', archive, cancelled,
                                  lambda **counts: observer(phase='Descargando MISP Galaxy', **counts)))
        unpacked = 0
        with tarfile.open(archive, 'r:gz') as bundle:
            for entry in bundle:
                if cancelled():
                    raise InterruptedError('Extracción cancelada.')
                relative = PurePosixPath(*PurePosixPath(entry.name).parts[1:])
                if not entry.isfile() or not relative.parts or '..' in relative.parts:
                    continue
                if any(':' in part or '\\' in part for part in relative.parts):
                    raise ValueError('Nombre de archivo no portátil en la instantánea.')
                if relative.suffix != '.json' and relative.name != 'LICENSE.md':
                    continue
                unpacked += entry.size
                if len(artifacts) >= 10000:
                    raise ValueError('La instantánea contiene demasiados archivos JSON.')
                if entry.size > LIMIT or unpacked > 512 * 1024 ** 2:
                    raise ValueError('La instantánea MISP supera el límite de extracción.')
                target = folder.joinpath(*relative.parts)
                if not target.resolve().is_relative_to(folder.resolve()):
                    raise ValueError('Ruta no válida en la instantánea de MISP.')
                target.parent.mkdir(parents=True, exist_ok=True)
                with closing(bundle.extractfile(entry)) as stream:
                    target.write_bytes(stream.read())
                artifacts.append({'path': str(target), 'sha256': digest(target), 'bytes': target.stat().st_size,
                                  'url': f'https://raw.githubusercontent.com/{repository}/{revision}/{relative}'})
    record = {'source': source, 'repository': repository, 'revision': revision,
              'attack_version': attack_version if source == 'mitre' else None,
              'retrieved_at': datetime.now(timezone.utc).isoformat(), 'artifacts': artifacts}
    write_json(receipt, record)
    return record
