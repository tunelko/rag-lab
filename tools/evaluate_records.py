"""Compare structured answers with hand-written reference SQL on the published snapshots.

Each case has a question and a reference query written independently of the planner and
the executor. The tool plans and runs the question, runs the reference, and reports any
difference. It only reads the knowledge store; it never calls a model or the network.

    python -m tools.evaluate_records --dataset data/evaluation/records-results-v1.jsonl

Case format (one JSON object per line):
    {"id": "...", "question": "...", "kind": "set" | "ordered" | "count" | "groups",
     "reference_sql": "SELECT ...", "today": "YYYY-MM-DD"}
`set` and `ordered` references return one column of identifiers; `count` returns one number;
`groups` returns (key, count) rows.
"""
from __future__ import annotations

import argparse
from datetime import date
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from knowledge_store import KnowledgeStore  # noqa: E402
import record_queries  # noqa: E402
import record_search  # noqa: E402


def evaluate(store, cases, *, limit=record_search.MAX_LIMIT):
    vendors = record_search.vocabulary(store)
    outcomes = []
    with store.connect() as db:
        for case in cases:
            plan = record_queries.plan(case['question'], today=case.get('today') or date.today(), vendors=vendors)
            outcome = {'id': case['id'], 'question': case['question'], 'kind': case['kind'], 'plan': plan.as_dict() if plan else None}
            rows = db.execute(case['reference_sql']).fetchall()
            if plan is None:
                outcome.update(ok=False, problem='no plan')
            elif plan.unresolved:
                outcome.update(ok=False, problem='names not in the published vocabulary', size=len(rows))
            else:
                result = record_search.search(store, plan, default_limit=limit)
                if case['kind'] == 'count':
                    expected, got = rows[0][0], result['total']
                elif case['kind'] == 'groups':
                    expected = sorted((str(row[0]), row[1]) for row in rows)
                    got = sorted((str(group['key']), group['count']) for group in result.get('groups', []))
                elif case['kind'] == 'ordered':
                    expected, got = [row[0] for row in rows], [record['cve_id'] for record in result.get('records', [])]
                else:
                    # The answer may be capped: the total must equal the reference size, and every
                    # identifier shown must belong to the reference set.
                    reference = {row[0] for row in rows}
                    shown = {record['cve_id'] for record in result.get('records', [])}
                    expected = {'size': len(reference), 'unexpected': []}
                    got = {'size': result.get('total'), 'unexpected': sorted(shown - reference)[:12]}
                    if len(shown) < min(len(reference), result.get('limit', len(shown))):
                        got['shown'] = len(shown)
                outcome.update(ok=expected == got, size=len(rows))
                if expected != got:
                    outcome['expected'], outcome['got'] = _short(expected), _short(got)
            outcomes.append(outcome)
    return outcomes


def _short(value, limit=12):
    return value if not isinstance(value, list) or len(value) <= limit else [*value[:limit], f'… ({len(value)} in total)']


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--dataset', type=Path, required=True)
    parser.add_argument('--data-dir', type=Path, default=ROOT / 'data' / 'knowledge')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    raw = args.dataset.read_bytes()
    cases = [json.loads(line) for line in raw.decode('utf-8').splitlines() if line.strip()]
    store = KnowledgeStore(args.data_dir / 'knowledge.sqlite3')
    outcomes = evaluate(store, cases)
    with store.connect() as db:
        sources = record_search.provenance(db)
    report = {'dataset': args.dataset.name, 'dataset_sha256': hashlib.sha256(raw).hexdigest(),
              'snapshots': {source['source_id']: source['snapshot_id'] for source in sources},
              'cases': len(outcomes), 'passed': sum(outcome['ok'] for outcome in outcomes),
              'empty_references': [outcome['id'] for outcome in outcomes if outcome.get('size') == 0],
              'failures': [outcome for outcome in outcomes if not outcome['ok']]}
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + '\n', encoding='utf-8')
    print(text)
    return 0 if report['passed'] == report['cases'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
