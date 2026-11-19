import unittest
from types import SimpleNamespace
from unittest.mock import patch

import shared_queries
from jobs_runtime import JobFailure
from shared_queries import (Passage, bare_reference, catalogue_selection, document_condition, graph_pairs, history_files, named_comparison, render,
                            requested_identifiers, resolve_files, wants_intersection)
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

    def test_a_request_to_be_shown_something_is_not_a_comparison(self):
        for question in ('Share with me the profile of Alpha', 'Please share the profile of Alpha',
                         'Can you share the Alpha report with us?', 'Share the requirements of alpha.md'):
            self.assertFalse(wants_intersection(question), question)
        for question in ('Which requirements does Alpha share with Beta?', 'What do Alpha and Beta share?',
                         'Share with me what Alpha and Beta have in common', 'Requirements shared by Alpha and Beta'):
            self.assertTrue(wants_intersection(question), question)

    def test_a_limit_shows_part_of_the_verified_matches_and_says_how_many_there_are(self):
        q=SimpleNamespace(language='es')
        labels=['Pliego Alfa','Pliego Beta']
        proof=lambda n:Passage('p.pdf',n,n,'evidencia').source('contracts',0,f'Cita {n}.')
        shown=[('Norma '+str(n),[proof(n),proof(n+10)]) for n in range(2)]
        cut=render(q,labels,shown,{'intersection_verified':5})
        self.assertTrue(cut['answer'].startswith('Se muestran **2 de 5** coincidencias verificadas.'))
        self.assertEqual((cut['metrics']['intersection_matches'],cut['metrics']['intersection_total']),(2,5))
        whole=render(q,labels,shown,{'intersection_verified':2})
        self.assertNotIn('Se muestran',whole['answer'])
        # The structured route reports its own total; a limit of zero is not an absence of matches.
        none=render(SimpleNamespace(language='en'),labels,[],{'knowledge_total':3})
        self.assertIn('**3** verified matches',none['answer'])
        self.assertNotIn('could not verify',none['answer'])

    def test_both_points_back_to_two_documents_or_to_none(self):
        files=['alpha.md','beta.md','gamma.md']
        turn=lambda role,content:SimpleNamespace(role=role,content=content)
        ask=lambda *turns:history_files(SimpleNamespace(history=list(turns)),files)
        self.assertEqual(ask(turn('user','Resume alpha.md y gamma.md'),turn('assistant','beta.md también existe')),['alpha.md','gamma.md'])
        self.assertEqual(ask(turn('user','Resume alpha.md'),turn('user','Resume beta.md')),['alpha.md','beta.md'])
        # The latest turn that names two wins over older names.
        self.assertEqual(ask(turn('user','Resume alpha.md'),turn('user','Compara beta.md y gamma.md')),['beta.md','gamma.md'])
        self.assertEqual(ask(turn('user','Resume alpha.md'),turn('user','Resume beta.md'),turn('user','Resume gamma.md')),[])
        self.assertEqual(ask(turn('user','Resume alpha.md'),turn('assistant','beta.md')),[])

    def ask(self, question, **body):
        job=self.client.post('/api/query',headers=self.headers,json={'question':question,'graph_retrieval':True,
                             'language':'en',**body}).json()['job_id']
        return wait_job(self.client,job)

    def test_a_quoted_condition_counts_only_as_words_the_user_wrote(self):
        # The validator alone, on the data an interpretation hands it; no inference is exercised here.
        question='What do the tenders published in 2024 have in common?'
        accept=lambda condition,named=():document_condition(condition,question,named)
        # The words come back as the user wrote them.
        self.assertEqual(accept('published in 2024'),'published in 2024')
        self.assertEqual(accept(' Published in 2024 '),'published in 2024')
        for rejected in ('published in 2023','in 20','in','What do the tenders published in 2024 have in common?','','  ','x'*201,
                         None,7,['published in 2024'],{'condition':'published in 2024'}):
            self.assertIsNone(accept(rejected),rejected)
        self.assertIsNone(accept('2024',['exp 2024 PPT.pdf']))
        self.assertEqual(document_condition('que llevan arroz','¿Qué tienen en común las recetas que llevan arroz?',()),'que llevan arroz')

    def test_the_register_proves_a_format_or_the_whole_selection_and_nothing_narrower(self):
        files=['a.pdf','b.PDF','c.md','d.markdown','e.txt']
        pdf,markdown,everything=('format','PDF',['a.pdf','b.PDF']),('format','Markdown',['c.md','d.markdown']),('all','',sorted(files))
        for question,expected in (
                ('¿Qué tienen en común los PDF de esta categoría?',pdf),('¿Qué requisitos comparten los archivos PDF?',pdf),
                ('¿En qué coinciden los documentos en formato PDF de la categoría?',pdf),('What do the PDF files have in common?',pdf),
                ('What do both PDFs share?',pdf),('¿Qué tienen en común los Markdown?',markdown),
                ('Do the .md files have anything in common?',markdown),('What do the TXT files have in common?',('format','TXT',['e.txt'])),
                ('What do all the documents in this category have in common?',everything),('¿Qué comparten todos los documentos?',everything),
                ('¿Qué tienen en común los documentos seleccionados?',everything),
                ('¿Los PDF de esta categoría tienen algo en común?',pdf),('What requirements are shared by the PDFs?',pdf),
                ('¿Qué requisitos de solvencia tienen en común los PDF?',pdf)):
            self.assertEqual(catalogue_selection(question,files),expected,question)
        # A date, a subject or a clause narrows the set beyond what the register proves: it is never dropped.
        for question in ('¿Qué tienen en común los PDF de 2024?','What do the PDFs about security have in common?',
                         'What do the 2024 PDFs have in common?','¿Qué tienen en común los documentos que mencionan pollo?',
                         '¿Qué tienen en común los PDF publicados en 2024 de esta categoría?','¿Qué tienen en común los PDF y los Markdown?',
                         '¿Qué tienen en común los documentos que comparten autor?','¿Qué tienen en común las recetas con pollo?',
                         'What do the documents from last year have in common?','¿Qué tienen en común los PDF de esta categoría publicados en 2024?',
                         # A filter outside the phrase narrows the set just the same.
                         'Entre los publicados en 2023, ¿qué tienen en común los PDF de esta categoría?',
                         'What do the PDFs in this category, excluding old manuals, have in common?',
                         '¿Qué tienen en común los PDF de esta categoría, salvo los escaneados?',
                         'Entre los publicados en 2023 qué tienen en común los PDF','¿Qué tienen en común los PDF salvo los escaneados?',
                         'What do the PDFs excluding old manuals have in common?','What do the PDFs have in common regarding 2024?',
                         '¿Qué tienen en común los PDF? Solo los de 2023','¿Qué normas de 2023 comparten los PDF?'):
            self.assertIsNone(catalogue_selection(question,files),question)

    def test_a_selection_by_format_compares_exactly_two_and_otherwise_gives_the_count(self):
        # Files of the real register; none is indexed, so no model is consulted on any of these paths.
        for name in ('alpha.md','beta.md','gamma.txt'):
            (self.service.documents/name).write_text('Plain text of '+name,'utf-8')
        with patch.object(shared_queries,'ask_model',side_effect=AssertionError('The register decides the selection')):
            pair=self.ask('What do the Markdown files have in common?')
            single=self.ask('What do the TXT files have in common?')
            none=self.ask('What do the PDF files have in common?')
            everything=self.ask('What do all the documents have in common?')
            spanish=self.ask('¿Qué tienen en común todos los documentos de esta categoría?',language='es')
        self.assertEqual(pair['metrics']['intersection_sides'],['alpha.md','beta.md'])
        self.assertEqual(pair['metrics']['intersection_selection'],{'kind':'format','format':'Markdown','population':3})
        self.assertNotIn('needs_clarification',pair['metrics'])
        self.assertEqual(single['metrics']['clarification_selection'],{'kind':'format','format':'TXT','members':1,'population':3})
        self.assertIn('Of the 3 documents in the selected sources, 1 is TXT (gamma.txt). A comparison needs two documents.',single['answer'])
        self.assertIn('0 are PDF.',none['answer'])
        self.assertEqual(everything['metrics']['clarification_selection']['members'],3)
        self.assertIn('I do not pick two for you',everything['answer'])
        self.assertIn('Las fuentes seleccionadas tienen 3 documentos (alpha.md, beta.md, gamma.txt). El cruce compara dos documentos cada vez y no elijo dos por ti.',spanish['answer'])
        for result in (single,none,everything,spanish):
            self.assertTrue(result['metrics']['needs_clarification'])
            self.assertEqual(result['sources'],[])
        # Inside a category the population is the category.
        topic=self.service.topics.create('Two texts')
        self.service.topics.assign(['alpha.md','gamma.txt'],topic)
        with patch.object(shared_queries,'ask_model',side_effect=AssertionError('The register decides the selection')):
            scoped=self.ask('What do all the documents in this category have in common?',topic_id=topic)
            narrowed=self.ask('What do the Markdown files have in common?',topic_id=topic)
        self.assertEqual(scoped['metrics']['intersection_sides'],['alpha.md','gamma.txt'])
        self.assertEqual(narrowed['metrics']['clarification_selection'],{'kind':'format','format':'Markdown','members':1,'population':2})

    def test_two_documents_in_scope_answer_a_bare_both_and_a_longer_question_is_checked_first(self):
        for question in ('¿Qué tienen ambos en común?','¿Qué tienen en común los dos?','¿Qué requisitos comparten ambos?',
                         '¿En qué coinciden las dos?','What do both have in common?','What do the two share?',
                         'What do they both have in common?','Do both of them share anything?'):
            self.assertTrue(bare_reference(question),question)
        for question in ('¿Qué tienen en común los dos pliegos de 2024?','What do the two tenders from 2024 have in common?',
                         '¿Qué tienen en común ambos, salvo los anexos?','¿Citan ambos la norma ISO 9001?',
                         'Entre los de 2023, ¿qué tienen ambos en común?','What do both have in common regarding 2024?'):
            self.assertFalse(bare_reference(question),question)
        for name in ('alpha.md','beta.md'):
            (self.service.documents/name).write_text('Plain text of '+name,'utf-8')
        # Bare: the register decides and no interpretation is asked for.
        with patch.object(shared_queries,'ask_model',side_effect=AssertionError('Two documents in scope are all it can mean')):
            bare=self.ask('What do both have in common?')
        self.assertEqual(bare['metrics']['intersection_sides'],['alpha.md','beta.md'])
        # Longer: nothing is compared until the question has been checked. A failure is injected, not an answer.
        with patch.object(shared_queries,'ask_model',side_effect=JobFailure('INTERSECTION_FORMAT','unusable')) as model:
            longer=self.ask('What do the two tenders from 2024 have in common?')
        self.assertEqual(model.call_count,1)
        self.assertEqual((longer['metrics']['planning_status'],longer['sources']),('invalid_plan',[]))
        self.assertNotIn('intersection_sides',longer['metrics'])
        # The same holds for a pair taken from the conversation when the question adds something new.
        (self.service.documents/'gamma.md').write_text('Plain text of gamma.md','utf-8')
        history=[{'role':'user','content':'Summarise alpha.md and gamma.md'}]
        with patch.object(shared_queries,'ask_model',side_effect=AssertionError('The conversation names the pair')):
            referred=self.ask('What do both have in common?',history=history)
        self.assertEqual(referred['metrics']['intersection_sides'],['alpha.md','gamma.md'])
        with patch.object(shared_queries,'ask_model',side_effect=JobFailure('INTERSECTION_FORMAT','unusable')) as model:
            restricted=self.ask('What do both have in common among those from 2024?',history=history)
        self.assertEqual(model.call_count,1)
        self.assertEqual(restricted['metrics']['planning_status'],'invalid_plan')
        self.assertNotIn('intersection_sides',restricted['metrics'])

    def test_a_failed_interpretation_is_said_and_a_provider_failure_is_an_error(self):
        # Failures are injected, not answers: what the model would say is never simulated here.
        for name in ('alpha.md','beta.md','gamma.md'):
            (self.service.documents/name).write_text('Plain text of '+name,'utf-8')
        question='What do the tenders published in 2024 have in common?'
        with patch.object(shared_queries,'ask_model',side_effect=JobFailure('INTERSECTION_FORMAT','unusable')):
            result=self.ask(question)
        self.assertEqual((result['metrics']['planning_status'],result['sources']),('invalid_plan',[]))
        for name in ('alpha.md','beta.md','gamma.md'):
            self.assertNotIn(name,result['answer'])
        with patch.object(shared_queries,'ask_model',side_effect=JobFailure('INTERSECTION_PROVIDER','offline')):
            job=self.ask(question)
        self.assertEqual((job['status'],job['error_info']['code']),('error','INTERSECTION_PROVIDER'))

    def test_names_outside_the_category_and_three_names_need_no_interpretation(self):
        for name in ('alpha.md','beta.md','gamma.md'):
            publish(self.service,name,'Board '+name[0].upper(),'DDR4')
        topic=self.service.topics.create('Two boards')
        self.service.topics.assign(['alpha.md','gamma.md'],topic)
        with patch.object(shared_queries,'ask_model',side_effect=AssertionError('The register already explains it')):
            outside=self.ask('What do alpha.md and beta.md have in common?',topic_id=topic)
            three=self.ask('What do alpha.md, beta.md and gamma.md have in common?')
        self.assertEqual(outside['metrics']['clarification_out_of_scope'],['beta.md'])
        self.assertEqual(outside['metrics']['clarification_named'],['alpha.md'])
        self.assertIn('Not in the selected category: beta.md',outside['answer'])
        self.assertEqual(outside['metrics']['planning_status'],'missing_operands')
        self.assertIn('You named 3 documents',three['answer'])
        self.assertEqual(three['sources'],[])
