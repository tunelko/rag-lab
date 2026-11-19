import unittest
from types import SimpleNamespace

from shared_queries import Passage, graph_pairs, named_comparison, render, requested_identifiers, resolve_files, wants_intersection
from graph_engine import canonical_groups, current_rows, mentions
from graph_jobs import graph_store
from tests.unit import test_graph_engine
from tests.unit.test_graph_engine import link, publish, wait_job


class SharedEvidenceTests(unittest.TestCase):
    setUp = test_graph_engine.GraphEngineTests.setUp
    tearDown = test_graph_engine.GraphEngineTests.tearDown

    def pairs(self, files):
        rows=current_rows(self.service,files)
        ids,_=canonical_groups(mentions(rows),graph_store(self.service).identity_decisions())
        return list(graph_pairs(rows,ids,files,True))

    def test_both_document_versions_and_verified_identity_required(self):
        files=['alpha.md','beta.md']
        left=publish(self.service,files[0],'Board Alpha','DDR4')
        right=publish(self.service,files[1],'Board Beta','DDR4')
        self.assertEqual(self.pairs(files),[])
        link(self.service,left['object'],right['object'])
        matches=self.pairs(files)
        self.assertEqual([(v,a['file'],b['file']) for _,v,a,b in matches],[('DDR4',*files)])
        (self.service.documents/files[1]).write_text('A new revision','utf-8')
        self.assertEqual(self.pairs(files),[])

    def test_negative_edge_and_out_of_scope_sides_cannot_complete_a_match(self):
        left=publish(self.service,'alpha.md','Board Alpha','DDR4')
        right=publish(self.service,'beta.md','Board Beta','DDR4',polarity='negative',text='Board Beta does not support DDR4.')
        link(self.service,left['object'],right['object'])
        self.assertEqual(self.pairs(['alpha.md','beta.md']),[])
        t=self.service.topics.create('One board')
        self.service.topics.assign(['alpha.md'],t)
        job=self.client.post('/api/query',headers=self.headers,json={'question':'What do alpha.md and beta.md have in common?',
                            'topic_id':t,'graph_retrieval':True}).json()['job_id']
        result=wait_job(self.client,job)
        self.assertTrue(result['metrics']['needs_clarification'])
        self.assertEqual(result['sources'],[])

    def test_citations_preserve_distinct_locations_and_one_sided_status(self):
        q=SimpleNamespace(language='es')
        labels=['Pliego Alfa','Pliego Beta']
        pdf=Passage('p.pdf',7,3,'evidencia').source('contracts',0,'Obligación explícita.')
        md=Passage('b.md',0,1,'evidencia').source('contracts',0,'Obligación corroborada.')
        paired=render(q,labels,[('Norma X',[pdf,md])],{})
        self.assertEqual([s['citation'] for s in paired['sources']],[1,2])
        self.assertTrue(paired['sources'][0]['url'].endswith('collection=contracts#page=7'))
        partial=render(q,labels,[('Norma X',[pdf,None])],{},[('Norma X',0,pdf)])
        self.assertEqual(partial['metrics']['intersection_matches'],0)
        self.assertIn('aparece en Pliego Alfa',partial['answer'])
        self.assertEqual(len(partial['sources']),1)

    def test_intent_and_document_identity(self):
        files=['exp__13058_2025__PPT.pdf','exp__2026-00238__PPT.pdf']
        self.assertTrue(named_comparison('¿Qué norma citan 13058_2025 y 2026-00238?',files))
        self.assertTrue(wants_intersection('¿Qué requisito tienen en común?'))
        self.assertFalse(wants_intersection('¿Cuál de ambos tiene más páginas?'))
        self.assertFalse(wants_intersection('What do Alpha and Beta not share?'))
        self.assertFalse(wants_intersection('¿Qué requisitos no comparten?'))
        self.assertEqual(resolve_files('¿Qué citan 13058_2025 y 2026-002389?',files),files[:1])
        self.assertEqual(requested_identifiers('¿Citan ambos 13058_2025 y 2026-00238 la norma ISO/IEC 20000-1?',files),
                         ['ISO/IEC 20000-1'])
