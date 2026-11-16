"""Summarize actual security artifacts without treating missing tests as passes."""
import json
from pathlib import Path


def main():
    print('### Actual security results\n')
    reports = sorted(Path('reports/security').glob('*/security-report.json'))
    if reports:
        report = json.loads(reports[-1].read_text(encoding='utf-8'))
        print('- HTTP/browser/real embeddings: '+json.dumps(report['summary'])+'.')
        print('- Real LLM: '+(report.get('model') or {}).get('name', 'NOT RUN; no model-security verdict')+'.')
    else:
        print('- HTTP/browser/real embeddings: NOT RUN or failed before producing a report.')
    path = Path('reports/security-audit/audit-summary.json')
    if path.exists():
        for row in json.loads(path.read_text(encoding='utf-8'))['results']:
            print('- '+row['id']+': '+row['status']+(' — '+json.dumps(row.get('severity')) if 'severity' in row else '')+'.')
    else:
        print('- Source/dependency audit: NOT RUN.')
    print('\nA controlled case is not complete OWASP coverage. Medium/low Bandit findings remain review items.')


if __name__ == '__main__':
    main()
