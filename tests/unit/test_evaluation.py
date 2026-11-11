"""Known-value tests for retrieval and answer evaluation metrics."""
from contextlib import redirect_stdout
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from evaluation import anls, aggregate_rankings, score_abstention, score_citations, score_ranking
import rag
from tools.evaluate_retrieval import adoption_verdict, paired_comparison, run_mode
from tools.prepare_lexical_index import backup_database, main as prepare_main, status
from vector_store import VectorStore


class EvaluationTests(unittest.TestCase):
    def test_binary_and_graded_ranking_metrics(self):
        qrels=[{'file':'a.pdf','page':1,'relevance':2},
               {'file':'b.pdf','page':2,'relevance':1}]
        hits=[{'id':'b','metadata':{'file':'b.pdf','page':2}},
              {'id':'x','metadata':{'file':'x.pdf','page':4}},
              {'id':'a','metadata':{'file':'a.pdf','page':1}}]
        result=score_ranking(hits,qrels,ks=(1,3))
        self.assertEqual(result['recall@1'],.5)
        self.assertEqual(result['precision@1'],1)
        self.assertEqual(result['mrr@1'],1)
        self.assertAlmostEqual(result['ndcg@1'],1/3)
        self.assertEqual(result['recall@3'],1)
        self.assertEqual(result['mrr@3'],1)
        self.assertAlmostEqual(result['ndcg@3'],2.5/(3+1/1.584962500721156),places=6)

    def test_duplicate_hits_do_not_count_the_same_page_twice(self):
        qrels=[{'file':'manual.pdf','page':8}]
        hits=[{'id':'one','metadata':{'file':'manual.pdf','page':8}},
              {'id':'two','metadata':{'file':'manual.pdf','page':8}}]
        result=score_ranking(hits,qrels,ks=(2,))
        self.assertEqual(result['relevant_retrieved'],1)
        self.assertEqual(result['precision@2'],.5)
        self.assertEqual(result['fragment_recall@2'],1)
        with self.assertRaisesRegex(ValueError,'one relevance granularity'):
            score_ranking(hits,[{'file':'manual.pdf'},{'file':'manual.pdf','page':8}],ks=(2,))

    def test_aggregate_latencies_and_answer_side_metrics(self):
        aggregate=aggregate_rankings([
            {'latency_ms':10,'metrics':{'recall@1':1,'relevant_total':1,'relevant_retrieved':1}},
            {'latency_ms':30,'metrics':{'recall@1':0,'relevant_total':1,'relevant_retrieved':0}},
        ])
        self.assertEqual(aggregate['recall@1'],.5)
        self.assertEqual(aggregate['latency_ms_p50'],20)
        self.assertAlmostEqual(anls('Core i7 12700K',['Core i7-12700K']),13/14)
        citations=score_citations([{'file':'manual.pdf','page':8},{'file':'wrong.pdf','page':1}],
                                  [{'file':'manual.pdf','page':8}])
        self.assertEqual(citations,{'citation_precision':.5,'citation_recall':1})
        abstention=score_abstention([
            {'answerable':False,'abstained':True},{'answerable':True,'abstained':False}])
        self.assertEqual(abstention['answer_decision_accuracy'],1)

    def test_explicit_lexical_preparation_has_verified_backup_and_rollback(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);db=root/'index';db.mkdir()
            config=rag.Config(db=db,collection='evaluation')
            store=VectorStore(db/'evaluation-vectors.sqlite3')
            store.upsert_vectors(['one'],['ATX_12V connector'],[{'file':'board.pdf'}],
                                 np.asarray([[1,0]],dtype=np.float32))
            backup=backup_database(config,store.path)
            self.assertTrue((backup/store.path.name).is_file())
            self.assertFalse(status(store)['lexical_index'])
            store.ensure_lexical_index()
            self.assertTrue(status(store)['lexical_index'])
            self.assertEqual(store.lexical_search('ATX_12V',1)[0]['id'],'one')
            with store.connect() as connection:
                self.assertEqual(connection.execute(
                    'SELECT id,docid FROM fts_rows').fetchone()[0],'one')
            store.remove_lexical_index()
            self.assertFalse(status(store)['lexical_index'])

    def test_paired_comparison_is_reproducible_and_adoption_needs_required_modes(self):
        results={
            'dense':{'queries':[{'id':'a','metrics':{'ndcg@4':.2}},
                                {'id':'b','metrics':{'ndcg@4':.4}}]},
            'hybrid':{'queries':[{'id':'a','metrics':{'ndcg@4':.3}},
                                 {'id':'b','metrics':{'ndcg@4':.5}}]},
        }
        first=paired_comparison(results,'dense','hybrid','ndcg@4')
        self.assertEqual(first,paired_comparison(results,'dense','hybrid','ndcg@4'))
        self.assertEqual(first['wins'],2)
        self.assertGreater(first['mean_delta_ci95'][0],0)
        self.assertEqual(adoption_verdict({},[],(1,4,8))['verdict'],'not_evaluated')

    def test_explicit_prepare_cli_verifies_and_noops_without_extra_backups(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);db=root/'index';db.mkdir()
            store=VectorStore(db/'cli-vectors.sqlite3')
            store.upsert_vectors(['one'],['ATX_12V'],[{'file':'board.pdf'}],[[1,0]])
            arguments=['prepare_lexical_index','--db',str(db),'--collection','cli']
            with patch('sys.argv',arguments),redirect_stdout(io.StringIO()):prepare_main()
            backups=list((root/'vector-backups').iterdir())
            self.assertEqual(len(backups),1)
            with patch('sys.argv',arguments),redirect_stdout(io.StringIO()):prepare_main()
            self.assertEqual(len(list((root/'vector-backups').iterdir())),1)
            with patch('sys.argv',arguments+['--remove']),redirect_stdout(io.StringIO()):prepare_main()
            self.assertEqual(len(list((root/'vector-backups').iterdir())),2)
            with patch('sys.argv',arguments+['--remove']),redirect_stdout(io.StringIO()):prepare_main()
            self.assertEqual(len(list((root/'vector-backups').iterdir())),2)

    def test_adoption_rule_is_executable_and_refuses_missing_strata(self):
        def mode(ndcg,recall,latency):
            groups={name:{'queries':5,'fragment_recall@4':recall}
                    for name in ('exact-identifier','cross-language','cjk')}
            return {'queries':[{'id':str(index),'metrics':{'ndcg@4':ndcg}}
                               for index in range(50)],
                    'summary':{'ndcg@4':ndcg,'fragment_recall@4':recall,
                               'latency_ms_p95':latency},'by_stratum':groups}
        results={'dense':mode(.5,.8,20),'hybrid':mode(.6,.8,40)}
        self.assertEqual(adoption_verdict(results,[{}]*50,(1,4,8))['verdict'],'pass')
        del results['hybrid']['by_stratum']['cjk']
        self.assertEqual(adoption_verdict(results,[{}]*50,(1,4,8))['verdict'],
                         'insufficient_data')
        results['hybrid']['summary']['ndcg@4'] = .4
        for item in results['hybrid']['queries']:
            item['metrics']['ndcg@4'] = .4
        self.assertEqual(adoption_verdict(results,[{}]*50,(1,4,8))['verdict'], 'fail')

    def test_run_mode_metrics_feed_the_real_adoption_verdict(self):
        class FakeStore:
            @staticmethod
            def embed_query(_question):
                return [1.0, 0.0]

            @staticmethod
            def dense_search_vector(_vector, _limit, **_options):
                return [
                    {'id':'wrong','text':'Un fragmento técnico incorrecto pero utilizable.',
                     'metadata':{'file':'wrong.pdf','page':1},'score':.9},
                    {'id':'right','text':'La evidencia técnica correcta para esta consulta.',
                     'metadata':{'file':'right.pdf','page':8},'score':.8},
                ]

            @staticmethod
            def hybrid_search(_question, _limit, **_options):
                return [
                    {'id':'right','text':'La evidencia técnica correcta para esta consulta.',
                     'metadata':{'file':'right.pdf','page':8},'score':.04},
                    {'id':'wrong','text':'Un fragmento técnico incorrecto pero utilizable.',
                     'metadata':{'file':'wrong.pdf','page':1},'score':.03},
                ]

        dataset = [
            {'id':str(index),'question':'Consulta técnica',
             'relevant':[{'file':'right.pdf','page':8}],
             'strata':['exact-identifier','cross-language','cjk']}
            for index in range(50)
        ]
        cutoffs = (1, 4, 8)
        dense = run_mode(FakeStore(), 'dense', dataset, ['right','wrong'], cutoffs)
        hybrid = run_mode(FakeStore(), 'hybrid', dataset, ['right','wrong'], cutoffs)
        self.assertIn('ndcg@4', dense['summary'])
        self.assertIn('mrr@4', dense['summary'])
        verdict = adoption_verdict({'dense':dense,'hybrid':hybrid}, dataset, cutoffs)
        self.assertEqual(verdict['verdict'], 'pass')


if __name__=='__main__':unittest.main()
