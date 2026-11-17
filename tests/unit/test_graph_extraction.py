"""Grounded plans use real source text, exact offsets and application-built IDs."""
import json
import unittest

from graph_extraction import materialize, passages, schema_for


def plan(evidence_id='E1'):
    return {'claims': [{'subject': 'Board', 'subject_kind': 'component', 'predicate': 'supports',
        'object': 'DDR4', 'object_kind': 'memory', 'evidence_id': evidence_id, 'basis': 'explicit'}]}


class GroundedPlanTests(unittest.TestCase):
    def test_quotes_and_entity_references_are_constructed_from_source(self):
        source = 'CPU socket\nLGA 1151\nThe board supports DDR4 memory.\n'
        evidence = passages(source)
        result = materialize(json.dumps(plan()), source, evidence)
        claim = result.assertions[0]
        self.assertEqual(claim.quote, source)
        self.assertEqual(source[claim.source_start:claim.source_end], claim.quote)
        self.assertEqual({entity.key for entity in result.entities}, {claim.subject, claim.object})
        self.assertEqual(schema_for(evidence)['$defs']['ClaimPlan']['properties']['evidence_id']['enum'], ['E1'])

    def test_nonexistent_evidence_and_model_written_quotes_are_rejected(self):
        source = 'This board does not support DDR5 memory.'
        with self.assertRaises(ValueError):
            materialize(json.dumps(plan('E99')), source, passages(source))
        payload = plan()
        payload['claims'][0]['quote'] = 'This board supports DDR5 memory.'
        with self.assertRaises(ValueError):
            materialize(json.dumps(payload), source, passages(source))

    def test_segments_preserve_source_and_identical_claims_do_not_duplicate(self):
        source = ('Distinct unchanged words on a source line.\n' * 70)
        evidence = passages(source)
        self.assertEqual(''.join(row['text'] for row in evidence.values()), source)
        payload = plan('E2')
        payload['claims'].append(dict(payload['claims'][0]))
        result = materialize(json.dumps(payload), source, evidence)
        self.assertEqual(len(result.assertions), 1)
        self.assertEqual(result.assertions[0].source_start, evidence['E2']['start'])
        self.assertEqual(result.assertions[0].quote, evidence['E2']['text'])
        changed = {key: dict(value) for key, value in evidence.items()}
        changed['E2']['text'] = 'Rewritten evidence must not be accepted.'
        with self.assertRaises(ValueError):
            materialize(json.dumps(payload), source, changed)


if __name__ == '__main__':
    unittest.main()
