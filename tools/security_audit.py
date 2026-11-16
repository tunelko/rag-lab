"""Scan actual source, installed Python packages and served JavaScript dependencies.

Scanners run in a separate tool environment. Nothing is installed or upgraded here.
Network failures, empty inventories and unaudited Python packages fail the audit.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def command(arguments, output):
    with output.open('w', encoding='utf-8') as log:
        result = subprocess.run(arguments, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                                timeout=600, check=False)
    return result.returncode


def static_scan(python, output):
    path = output/'bandit.json'
    path.unlink(missing_ok=True)
    code = command([str(python), '-m', 'bandit', '-r', 'app', 'install', 'tools',
                    '-f', 'json', '-o', str(path)], output/'bandit.log')
    if code not in {0, 1} or not path.is_file():
        raise RuntimeError('Bandit failed; see bandit.log.')
    report = json.loads(path.read_text(encoding='utf-8'))
    if report.get('errors') or report.get('metrics', {}).get('_totals', {}).get('loc', 0) < 1:
        raise RuntimeError('Bandit did not scan the source successfully.')
    findings = report['results']
    high = [row for row in findings if row['issue_severity'] == 'HIGH']
    # Medium and low findings remain visible as review items, never a clean scan.
    return {'status': 'fail' if high else 'review' if findings else 'pass',
            'blocking': bool(high), 'risks': ['A03:2025', 'A05:2025', 'LLM04:2026'],
            'gate': 'All HIGH severity findings block, regardless of confidence.',
            'findings': len(findings), 'severity': {
                level: sum(row['issue_severity'] == level for row in findings)
                for level in ['HIGH', 'MEDIUM', 'LOW']}, 'report': path.name}


def dependencies(python, runtime, output):
    probe = subprocess.run([str(runtime), '-c',
        'import json,sysconfig;print(json.dumps(sorted(set(sysconfig.get_paths()[k] for k in ("purelib","platlib")))))'],
        cwd=ROOT, capture_output=True, text=True, timeout=30, check=True)
    paths = json.loads(probe.stdout)
    if not paths or any(not Path(path).is_dir() for path in paths):
        raise RuntimeError('Cannot locate the actual application site-packages.')
    # OSV accepts the installed official CPU/CUDA version, including +cpu/+cu130.
    # PyPI's per-release endpoint otherwise skips those builds as unknown releases.
    path = output/'dependencies.json'
    path.unlink(missing_ok=True)
    arguments = [str(python), '-m', 'pip_audit', '--strict', '--vulnerability-service', 'osv', '--format', 'json',
                 '--output', str(path), '--progress-spinner', 'off']
    for site_path in paths:
        arguments.extend(['--path', site_path])
    code = command(arguments, output/'dependencies.log')
    if code not in {0, 1} or not path.is_file():
        raise RuntimeError('pip-audit could not complete; see dependencies.log.')
    report = json.loads(path.read_text(encoding='utf-8'))
    inventory = report.get('dependencies', [])
    if not inventory:
        raise RuntimeError('An empty installed-package inventory is not a successful audit.')
    skipped = [row for row in inventory if row.get('skip_reason')]
    vulnerable = [row for row in inventory if row.get('vulns')]
    if skipped:
        raise RuntimeError('Packages could not be audited: '+', '.join(row['name'] for row in skipped))
    if code and not vulnerable:
        raise RuntimeError('pip-audit failed without a complete vulnerability result; see dependencies.log.')
    return {'status': 'fail' if vulnerable else 'pass', 'blocking': bool(vulnerable),
            'risks': ['A03:2025', 'LLM04:2026'], 'installed_packages': len(inventory),
            'service': 'OSV', 'site_packages': paths, 'vulnerable_packages': len(vulnerable),
            'advisories': sum(len(row['vulns']) for row in vulnerable), 'report': path.name}


def vendor_hashes():
    root = ROOT/'web'/'vendor'
    packages = json.loads((root/'manifest.json').read_text(encoding='utf-8'))
    checked = 0
    for package in packages:
        for item in package['files']:
            path = (root/item['file']).resolve()
            if not path.is_relative_to(root.resolve()):
                raise RuntimeError('A vendored asset escapes its directory.')
            if hashlib.sha256(path.read_bytes()).hexdigest() != item['sha256']:
                return {'status': 'fail', 'blocking': True, 'file': item['file'],
                        'risks': ['A03:2025', 'A08:2025', 'LLM04:2026']}
            checked += 1
    if not checked:
        raise RuntimeError('No vendored assets were checked.')
    return {'status': 'pass', 'blocking': False, 'files_checked': checked,
            'risks': ['A03:2025', 'A08:2025', 'LLM04:2026']}


def vendor_advisories(output):
    packages = json.loads((ROOT/'web'/'vendor'/'manifest.json').read_text(encoding='utf-8'))
    payload = {'queries': [{'package': {'name': row['name'], 'ecosystem': 'npm'},
                            'version': row['version']} for row in packages]}
    request = urllib.request.Request('https://api.osv.dev/v1/querybatch',
        data=json.dumps(payload).encode(), headers={'Content-Type': 'application/json',
            'User-Agent': 'Musa-Security-Audit/1.0'}, method='POST')
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=60) as response:
        data = json.load(response)
    if len(data.get('results', [])) != len(packages):
        raise RuntimeError('The OSV advisory response was incomplete.')
    result = [{'name': package['name'], 'version': package['version'],
               'vulnerabilities': response.get('vulns', [])}
              for package, response in zip(packages, data['results'])]
    (output/'javascript-advisories.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    count = sum(len(row['vulnerabilities']) for row in result)
    return {'status': 'fail' if count else 'pass', 'blocking': count > 0,
            'risks': ['A03:2025', 'LLM04:2026', 'LLM10:2026'],
            'packages': len(result), 'advisories': count, 'report': 'javascript-advisories.json'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tool-python', type=Path, required=True)
    parser.add_argument('--runtime-python', type=Path, default=Path(sys.executable))
    parser.add_argument('--scope', choices=['all', 'static', 'dependencies'], default='all')
    parser.add_argument('--output-dir', type=Path, default=ROOT/'reports'/'security-audit')
    args = parser.parse_args()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    checks = []
    if args.scope in {'all', 'static'}:
        checks.extend([('source-bandit', lambda: static_scan(args.tool_python.resolve(), output)),
                       ('vendor-integrity', vendor_hashes)])
    if args.scope in {'all', 'dependencies'}:
        checks.extend([('python-advisories', lambda: dependencies(args.tool_python.resolve(), args.runtime_python.resolve(), output)),
                       ('javascript-advisories', lambda: vendor_advisories(output))])
    results = []
    for identifier, operation in checks:
        try:
            row = {'id': identifier, **operation()}
        except Exception as error:
            row = {'id': identifier, 'status': 'error', 'blocking': True,
                   'detail': type(error).__name__+': '+str(error)[:1500]}
        results.append(row)
        print(json.dumps(row), flush=True)
    report = {'schema': 1, 'tested_at': datetime.now(timezone.utc).isoformat(),
              'results': results, 'blocking': any(row['blocking'] for row in results)}
    (output/'audit-summary.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    return int(report['blocking'])


if __name__ == '__main__':
    raise SystemExit(main())
