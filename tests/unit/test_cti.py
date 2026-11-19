"""CTI parsing/identity/relations and actual SQLite; no model doubles."""
from pathlib import Path
import tempfile
import unittest

from cti_records import normalize, export, public_id, sample_documents
from cti_sources import write_json, digest
from knowledge_store import KnowledgeQuery, KnowledgeStore, direct_name_list
from document_evidence import EvidenceStore
from comparison_planning import ComparisonClarification
from cti_chat import ComparisonPlan, ComparisonReview
from pydantic import ValidationError
from web_app import Query


def fixture(root):
    source = root / 'enterprise-attack' / 'enterprise-attack-20.1.json'
    objects = [
        {'type': 'intrusion-set', 'id': 'intrusion-set--one', 'name': 'APT29', 'aliases': ['Cozy Bear', 'Pepe'],
         'external_references': [{'source_name': 'mitre-attack', 'external_id': 'G0016'}]},
        {'type': 'intrusion-set', 'id': 'intrusion-set--two', 'name': 'Different actor', 'aliases': ['Pepe'],
         'external_references': [{'source_name': 'mitre-attack', 'external_id': 'G9999'}]},
        {'type': 'attack-pattern', 'id': 'attack-pattern--one', 'name': 'PowerShell',
         'external_references': [{'source_name': 'mitre-attack', 'external_id': 'T1059.001'}]},
        {'type': 'attack-pattern', 'id': 'attack-pattern--old', 'name': 'Old technique', 'revoked': True,
         'external_references': [{'source_name': 'mitre-attack', 'external_id': 'T9999'}]},
        {'type': 'tool', 'id': 'tool--one', 'name': 'Example tool',
         'external_references': [{'source_name': 'mitre-attack', 'external_id': 'S9999'}]},
        {'type': 'campaign', 'id': 'campaign--one', 'name': 'Documented campaign',
         'external_references': [{'source_name': 'mitre-attack', 'external_id': 'C9999'}]},
    ]
    for number, (source_ref, target_ref, relationship) in enumerate([
        ('intrusion-set--one', 'attack-pattern--one', 'uses'),
        ('intrusion-set--two', 'attack-pattern--one', 'uses'),
        ('intrusion-set--one', 'attack-pattern--old', 'uses'),
        ('intrusion-set--one', 'tool--one', 'uses'),
        ('campaign--one', 'attack-pattern--one', 'uses'),
        ('campaign--one', 'intrusion-set--one', 'attributed-to'),
        ('intrusion-set--one', 'missing--one', 'uses'),
    ]):
        objects.append({'type': 'relationship', 'id': 'relationship--' + str(number),
                        'source_ref': source_ref, 'target_ref': target_ref, 'relationship_type': relationship,
                        'description': 'Source documents the observed procedure.', 'modified': '2026-01-01T00:00:00Z',
                        'external_references': [{'url': 'https://example.test/evidence'}]})
    write_json(source, {'type': 'bundle', 'objects': objects})
    (root / 'LICENSE.txt').write_text('MITRE license fixture', 'utf-8')
    return {'source': 'mitre', 'revision': 'a' * 40, 'attack_version': '20.1',
            'artifacts': [{'path': str(source), 'url': 'https://example.test/enterprise-attack/enterprise-attack-20.1.json', 'sha256': digest(source)},
                          {'path': str(root / 'LICENSE.txt'), 'url': 'https://example.test/LICENSE.txt'}]}


class CtiTests(unittest.TestCase):
    def test_condition_is_a_planning_outcome_outside_the_executable_language(self):
        query = Query(question='What do the campaigns mentioned in these reports have in common?', language='en')
        plan = ComparisonPlan(operands=[], membership_condition='mentioned in these reports', direction='both', relationship='').resolve(query)
        self.assertIsInstance(plan, ComparisonClarification)
        answer = plan.answer(query, {'planning_attempts': 1}, 'groups')
        self.assertEqual(answer['metrics']['planning_status'], 'selection_by_condition')
        self.assertEqual(answer['sources'], [])
        self.assertIn('condition', answer['answer'])
        with self.assertRaises(ValidationError):
            KnowledgeQuery.model_validate({'operation': 'compare_selection'})

    def test_executable_intersections_require_distinct_user_grounded_operands(self):
        query = Query(question='What do Alpha and Beta have in common?')
        for names in ([], ['Alpha'], ['Alpha', 'ALPHA'], ['Alpha', 'Be'], ['Alpha', 'Invented']):
            with self.subTest(names=names):
                outcome = ComparisonPlan(membership_condition=None, operands=names, direction='both', relationship='').resolve(query)
                self.assertEqual(outcome.reason, 'missing_operands')
        outcome = ComparisonPlan(membership_condition=None, operands=['Alpha','Beta'], result_kinds=['tool'], direction='outgoing', relationship='uses').resolve(query)
        self.assertIsInstance(outcome, KnowledgeQuery)
        self.assertEqual(outcome.entities, ['Alpha', 'Beta'])
        self.assertEqual(outcome.target_types, ['tool'])
        with self.assertRaises(ValueError):
            ComparisonPlan(membership_condition=None, operands=['Alpha','Beta'], result_names=['Imaginary'], direction='both', relationship='').resolve(query)
        with self.assertRaises(ValidationError):
            KnowledgeQuery(operation='intersection', entities=['Alpha'])

    def test_comparison_exposes_unavailable_filters_and_verifies_quoted_evidence(self):
        query = Query(question='What did Alpha and Beta share in 2023?')
        draft = ComparisonPlan(operands=['Alpha','Beta'], membership_condition='in 2023', direction='both', relationship='')
        self.assertEqual(draft.resolve(query).reason, 'unsupported_restriction')
        review = ComparisonReview(faithful=False, missing_quote='in 2023')
        self.assertEqual(review.clarification(query), draft.resolve(query))
        for quote in ('', 'in 2024'):
            with self.assertRaises(ValueError):
                draft.model_copy(update={'membership_condition':quote}).resolve(query)
        self.assertIsNone(ComparisonReview(faithful=True, missing_quote=None).clarification(query))

    def test_followup_grounds_names_in_latest_user_turn_and_recognizes_identifier_separators(self):
        query = Query(question='What do they share?', history=[
            {'role':'user','content':'Compare Alpha and Beta.'},
            {'role':'user','content':'Now compare lab-7 and lab-8.'},
            {'role':'assistant','content':'What about Gamma?'}])
        plan = ComparisonPlan(operands=['Lab7','Lab8'], membership_condition=None, direction='both', relationship='')
        self.assertIsInstance(plan.resolve(query), KnowledgeQuery)
        for name in ('Gamma', 'Alpha'):
            self.assertEqual(plan.model_copy(update={'operands':[name,'Lab8']}).resolve(query).reason, 'missing_operands')
        with self.assertRaises(ValidationError):
            ComparisonPlan(operands=['Lab7','Lab8'], membership_condition=None, result_names=[' '], direction='both', relationship='')

    def test_name_catalogue_respects_category_before_deduplication_and_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rows, _ = normalize([fixture(root)])
            groups = [r for r in rows if r['type'] == 'intrusion-set']
            def extra(n, name, canonical):
                return {**groups[0], 'id': f'test-{n}', 'canonical_id': canonical,
                        'name': name, 'source': 'misp', 'document': f'groups/{n}.md'}
            variants = [extra(1, 'Alternative name', groups[0]['canonical_id']),
                        extra(2, 'Independent actor', 'misp:independent'),
                        extra(3, 'independent actor', 'misp:homonym'),
                        {**extra(4, 'Retired', 'misp:retired'), 'active': False}]
            db = KnowledgeStore(root / 'knowledge.sqlite3')
            db.publish(rows + variants, {})
            plan = direct_name_list('nombrs de grupos, hasta 100')
            self.assertEqual(plan.limit, 100)
            self.assertEqual(db.query(plan)['total'], len(groups) + 1)
            scope = {'cti/' + r['document'] for r in variants}
            scoped = db.query(plan, documents=scope)
            self.assertEqual([r['name'] for r in scoped['rows']], ['Alternative name', 'Independent actor'])
            one = db.query(direct_name_list('list up to 1 APT group names'), documents=scope)
            self.assertEqual((len(one['rows']), one['total']), (1, 2))
            self.assertEqual(db.query(plan, documents=set())['total'], 0)
            self.assertEqual(db.query(plan.model_copy(update={'source':'mitre'}), documents=scope)['rows'], [])
            for qualified in ('nombres de grupos de China, hasta 100', 'nombre de APT 29',
                              'list groups related to T1059', 'dame el nombre de APT-29'):
                self.assertIsNone(direct_name_list(qualified))

    def test_citations_keep_their_original_content_after_an_update(self):
        with tempfile.TemporaryDirectory() as directory:
            store = EvidenceStore(Path(directory) / 'evidence.sqlite3')
            first = store.put('procedure.md', 'Original procedure and citation.')
            second = store.put('procedure.md', 'Updated procedure and citation.')
            self.assertNotEqual(first, second)
            self.assertEqual(store.put('procedure.md', 'Original procedure and citation.'), first)
            self.assertEqual(store.get(first)['text'], 'Original procedure and citation.')
            self.assertEqual(store.get(second)['text'], 'Updated procedure and citation.')
            with self.assertRaises(ValueError):
                store.get('not-a-citation')

    def test_external_ids_are_not_guessed_from_stix_or_foreign_references(self):
        self.assertIsNone(public_id({'id': 'intrusion-set--G0016', 'external_references': [
            {'source_name': 'other', 'external_id': 'G0016'}]}))

    def test_revoked_and_dangling_relationships_are_historical_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rows, summary = normalize([fixture(root)])
            self.assertEqual(summary['active_relationships'], 5)
            self.assertEqual(summary['unresolved_relationships'], 1)
            self.assertEqual(summary['inactive_relationships'], 2)
            self.assertEqual(len(rows), 13)
            self.assertEqual(len(sample_documents(rows, 3)), 3)
            self.assertEqual([row['type'] for row in sample_documents(rows, 3)], ['intrusion-set', 'intrusion-set', 'attack-pattern'])
            output = export(rows, root / 'documents', [fixture(root)])
            self.assertEqual(output['documents'], 11)
            self.assertFalse(any('T9999' in name for name in output['expected']))
            evidence = next(row for row in rows if row['type'] == 'relationship' and row['active'])
            text = (root / 'documents' / evidence['document']).read_text('utf-8')
            self.assertIn('T1059.001', text)
            self.assertIn('https://example.test/evidence', text)
            self.assertIn('Source documents the observed procedure.', text)
            self.assertIn('20.1', text)

    def test_exact_relations_reverse_lookup_intersection_and_no_alias_merge(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rows, _ = normalize([fixture(root)])
            store = KnowledgeStore(root / 'knowledge.sqlite3')
            store.publish(rows, {'attack_version': '20.1'})
            techniques = store.query(KnowledgeQuery(operation='neighbors', entities=['G0016'], target_type='attack-pattern'))
            self.assertEqual([row['id'] for row in techniques['rows']], ['T1059.001'])
            self.assertTrue(techniques['rows'][0]['evidence'][0]['references'])
            groups = store.query(KnowledgeQuery(operation='neighbors', entities=['T1059.001'], target_type='intrusion-set', direction='incoming'))
            self.assertEqual([row['id'] for row in groups['rows']], ['G0016', 'G9999'])
            shared = store.query(KnowledgeQuery(operation='intersection', entities=['G0016', 'G9999'], target_type='attack-pattern'))
            self.assertEqual(shared['total'], 1)
            same = store.query(KnowledgeQuery(operation='intersection', entities=['G0016','Cozy Bear']))
            self.assertEqual(same['status'], 'same_entity')
            self.assertEqual(store.query(KnowledgeQuery(operation='entity', entities=['Pepe']))['status'], 'ambiguous')
            self.assertEqual(store.query(KnowledgeQuery(operation='entity', entities=["';DROP TABLE edges;--"]))['status'], 'not_found')
            self.assertEqual(store.query(KnowledgeQuery(operation='entity', entities=['unknown']))['status'], 'not_found')
            tools = store.query(KnowledgeQuery(operation='neighbors', entities=['APT29'], target_type='tool'))
            self.assertEqual(tools['rows'][0]['id'], 'S9999')
            self.assertEqual(store.query(KnowledgeQuery(operation='entity', entities=['APT-29']))['entities'][0]['canonical'], 'G0016')
            self.assertEqual(store.query(KnowledgeQuery(operation='entity', entities=['Cozy']))['entities'][0]['canonical'], 'G0016')
            self.assertEqual(store.query(KnowledgeQuery(operation='entity', entities=['mitre:intrusion']))['status'], 'ambiguous')
            selected = store.query(KnowledgeQuery(operation='evidence', entities=['G0016'], targets=['T1059.001']))
            self.assertEqual([row['id'] for row in selected['rows']], ['T1059.001'])
            software = store.query(KnowledgeQuery(operation='neighbors', entities=['G0016'], target_types=['tool', 'malware']))
            self.assertEqual([row['id'] for row in software['rows']], ['S9999'])
            campaigns = store.query(KnowledgeQuery(operation='neighbors', entities=['C9999'], target_type='attack-pattern'))
            self.assertEqual(campaigns['rows'][0]['id'], 'T1059.001')
            # Republishing the same snapshot changes no identity or counts.
            store.publish(rows, {'attack_version': '20.1'})
            self.assertEqual(store.query(KnowledgeQuery(operation='neighbors', entities=['G0016'], target_type='tool'))['total'], 1)


if __name__ == '__main__':
    unittest.main()
