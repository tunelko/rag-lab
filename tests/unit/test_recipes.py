"""Recipe adapter contracts: offline fixtures, real files/SQLite and no running library."""
import copy
import csv
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx
import pymupdf
import yaml

from tools.recipes import catalog, extractors, models, pipeline, sources
from tools.recipes.deduplicate import deduplicate
from tools.recipes.integration import private_index, read_export, verify
from tools.recipes.reports import reports


def fixture(identifier='one', **changes):
    values=dict(recipe_id=identifier,titulo='Patatas con cebolla',ingredientes=['500 g de patatas','una cebolla grande'],
        elaboracion=['Pelar las patatas y cocerlas lentamente con la cebolla hasta que estén tiernas.'],
        source_dataset='fixture',source_url='https://example.test/'+identifier,source_file='raw/fixture.json',
        source_record=identifier,source_url_kind='record',license_status='test-only',
        original_record={'original':'Texto íntegro, sin cambios.'},ingredient_segmentation='list',extraction_method='fixture')
    values.update(changes)
    return models.validate(models.normalize(models.Recipe(**values))).model_dump(mode='json')


class RecipeTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory();self.output=Path(self.temporary.name)
    def tearDown(self):self.temporary.cleanup()

    def prepare(self):
        source=sources.SOURCES[0];revision='fixture'
        root=self.output/'raw'/source.id/revision;root.mkdir(parents=True)
        payload=[{'id':i,'nombre':'Plato de prueba '+str(i),'ingredientes':['500 g de patatas','una cebolla grande'],
                  'instrucciones':'Pelar las patatas y cocerlas con la cebolla durante veinte minutos.'} for i in range(3)]
        path=root/'recetas.json';sources.write_json(path,payload)
        (root/'README.md').write_text('CC-BY-4.0',encoding='utf-8')
        info={'id':source.id,'kind':'hf','status':'available','revision':revision,'homepage':'https://example.test/dataset',
              'download_status':'downloaded','main_path':str(path),'artifacts':[{'path':str(path),'sha256':sources.digest(path),'bytes':path.stat().st_size}]}
        sources.write_json(self.output/'cache'/f'{source.id}-discovery.json',info)
        sources.write_json(root/'download.json',info)
        for phase in ('extract','normalize','validate'):pipeline.stage_sources(self.output,phase,[source])
        pipeline.deduplication(self.output,[source]);pipeline.export(self.output)
        return source,path

    def test_quantities_units_and_regional_names_preserve_original_text(self):
        for text,name,quantity,unit in [('500 gr de patatas','patata',500,'g'),('2 cucharadas de aceite de oliva','aceite de oliva',2,'cucharada'),
                ('una cebolla grande','cebolla',1,'unidad'),('sal al gusto','sal',None,None),('1 1/2 kg de fabes','fabes',1.5,'kg'),
                ('0,5 l de agua','agua',.5,'l')]:
            with self.subTest(text=text):
                parsed=models.ingredient(text)
                self.assertEqual((parsed.original,parsed.ingrediente,parsed.cantidad,parsed.unidad),(text,name,quantity,unit))

    def test_multiline_csv_encoding_and_decimal_delimiters(self):
        path=self.output/'fixture.csv'
        with path.open('w',encoding='utf-16',newline='') as output:
            writer=csv.writer(output,delimiter=';');writer.writerow(['title','ingredients','steps'])
            writer.writerow(['Plato','1,5 kg de patatas\nSal al gusto','Lavar.\nCocer despacio.'])
        rows=list(extractors.csv_rows(path));self.assertEqual(len(rows),1)
        self.assertEqual(rows[0][2:],('utf-16',';'))
        self.assertEqual(rows[0][1]['ingredients'],'1,5 kg de patatas\nSal al gusto')
        items,method=extractors.list_field('1,5 kg de patatas, sal al gusto',commas=True)
        self.assertEqual(items,['1,5 kg de patatas','sal al gusto']);self.assertEqual(method,'comma-heuristic')

    def test_literal_lists_layout_blocks_and_untrusted_literal_are_not_executed(self):
        items,method=extractors.list_field("['1 huevo', 'Sal, al gusto']")
        self.assertEqual((items,method),(['1 huevo','Sal, al gusto'],'list'))
        self.assertEqual(extractors.list_field('1 huevo      100 g harina      Sal')[0],['1 huevo','100 g harina','Sal'])
        marker=self.output/'executed.txt'
        payload="[__import__('pathlib').Path("+repr(str(marker))+").touch()]"
        self.assertEqual(extractors.list_field(payload)[1],'unsplit');self.assertFalse(marker.exists())

    def test_flat_text_does_not_become_a_single_counted_ingredient_or_single_quantity(self):
        text='2 patatas 1 cebolla sal pimienta'
        items,method=extractors.list_field(text)
        row=models.normalize(models.Recipe.model_validate(fixture('flat',ingredientes=items,ingredient_segmentation=method)))
        self.assertIsNone(row.ingredient_count);self.assertIsNone(row.ingredientes_normalizados[0].cantidad)
        self.assertEqual(row.ingredientes,[text])
        self.assertIn('aggregate_not_single_recipe',fixture('aggregate',titulo='2 recetas fáciles y rápidas')['errors'])

    def test_stable_ids_and_shared_book_urls_do_not_merge_distinct_recipes(self):
        self.assertEqual(models.stable_id('a','123','x','Before'),models.stable_id('a','123','y','After'))
        self.assertNotEqual(models.stable_id('a','123','x','Before'),models.stable_id('b','123','x','Before'))
        left=fixture('left',source_url='https://example.test/book',source_url_kind='document')
        right=fixture('right',titulo='Otro plato',ingredientes=['200 g arroz'],source_url='https://example.test/book',source_url_kind='document')
        masters,_,events,_=deduplicate([left,right],{'fixture':0})
        self.assertEqual((len(masters),len(events)),(2,0))

    def test_exact_url_duplicates_keep_attribution_and_quantity_variants_survive(self):
        left=fixture('a');same=fixture('b',source_url='https://example.test/a?utm_source=ignored',source_dataset='second')
        variant=fixture('c',ingredientes=['800 g de patatas','una cebolla grande'])
        masters,review,events,_=deduplicate([left,same,variant],{'fixture':0,'second':1})
        self.assertEqual((len(masters),len(review),len(events)),(2,0,1))
        master=next(row for row in masters if row['recipe_id']=='a');self.assertEqual(len(master['provenance']),2)
        self.assertEqual({row['recipe_id'] for row in masters},{'a','c'})

    def test_original_fields_survive_normalization_and_frontmatter_is_safe_yaml(self):
        original={'ingredients':'500 gr de patatas\r\nSal','unknown_field':'preservar: sí','quantity':'½'}
        row=fixture(original_record=copy.deepcopy(original),raciones=None)
        self.assertEqual(row['original_record'],original)
        markdown=pipeline.markdown(row);front=yaml.safe_load(markdown.split('---',2)[1])
        self.assertEqual(front['recipe_id'],row['recipe_id']);self.assertIsNone(front['raciones'])
        self.assertIn('# Patatas con cebolla',markdown)
        for item in row['ingredientes']+row['elaboracion']:self.assertIn(item,markdown)
        export=self.output/'recipe.md'
        row['titulo']='Patatas --- con cebolla';export.write_text(pipeline.markdown(row),encoding='utf-8')
        self.assertEqual(read_export(export)[0].metadata['titulo'],row['titulo'])

    def test_country_language_and_unknown_ingredient_counts_are_not_invented(self):
        row=fixture(pais_original='MEX',ingredient_segmentation='comma-heuristic')
        self.assertEqual(row['pais_origen'],'México');self.assertIsNone(row['ingredient_count'])
        unknown=fixture();self.assertIsNone(unknown['pais_origen'])
        self.assertEqual(extractors.instruction_language(['Heat the oil and add the onion. Then stir the mixture with the spoon.']),'en')
        self.assertEqual(extractors.instruction_language(['Calentar el aceite y añadir la cebolla con sal para cocer la mezcla.']),'es')
        damaged=fixture(titulo='',idioma='en')
        self.assertTrue(damaged['needs_review']);self.assertIn('empty_title',damaged['errors'])

    def test_reexecution_reuses_preparation_and_staging_never_touches_library(self):
        source,raw=self.prepare();raw_hash=sources.digest(raw)
        prepared=list(pipeline.records(self.output/'rag'/'ingestion.jsonl'))
        self.assertEqual(len(prepared),3)
        for row in prepared:
            self.assertEqual((self.output/row['markdown_path']).read_text(encoding='utf-8'),row['embedding_text'])
            self.assertIn(row['elaboracion'][0],row['embedding_text'])
        result=pipeline.stage_for_ingestion(self.output,limit=2)
        self.assertEqual(result['documents'],2);self.assertFalse(result['production_library_modified'])
        manifest=json.loads((self.output/'rag/manifest.json').read_text(encoding='utf-8'))
        timestamps={item['path']:(self.output/item['path']).stat().st_mtime_ns for item in manifest['records']}
        for phase in ('extract','normalize','validate'):
            self.assertTrue(pipeline.stage_sources(self.output,phase,[source])[0]['reused'])
        self.assertTrue(pipeline.deduplication(self.output,[source])['reused'])
        self.assertEqual(pipeline.export(self.output)['written'],0)
        self.assertEqual(timestamps,{item['path']:(self.output/item['path']).stat().st_mtime_ns for item in manifest['records']})
        self.assertEqual(sources.digest(raw),raw_hash)
        target=self.output/manifest['records'][0]['path'];target.write_text('changed',encoding='utf-8')
        with self.assertRaises(ValueError):pipeline.stage_for_ingestion(self.output)

    def test_typed_filters_exclude_unknown_counts_and_use_sql_parameters(self):
        source,_=self.prepare()
        with pipeline.database(self.output) as connection:
            connection.execute('UPDATE recipes SET ingredient_count=NULL WHERE recipe_id=(SELECT recipe_id FROM recipes LIMIT 1)')
        self.assertEqual(catalog.select(self.output,max_ingredients=4)['total'],2)
        self.assertEqual(catalog.select(self.output,country="España' OR 1=1--")['total'],0)
        self.assertEqual(catalog.select(self.output,ingredients=['patatas','cebolla'])['total'],3)

    def test_pdf_span_extraction_keeps_physical_page_and_excludes_english(self):
        source=sources.SOURCES[-1];path=self.output/'raw'/source.id/'original'/source.filename;path.parent.mkdir(parents=True)
        with pymupdf.open() as document:
            page=document.new_page(width=964,height=680)
            page.insert_text((177,350),'Patatas cocidas',fontsize=32)
            page.insert_text((177,410),'2 patatas',fontsize=11)
            page.insert_text((331,410),'2 potatoes',fontsize=11)
            page.insert_text((520,280),'Cocer las patatas con agua y sal durante veinte minutos.',fontsize=11)
            page.insert_text((520,350),'Boil potatoes with water and salt for twenty minutes.',fontsize=9)
            page.insert_text((35,590),'6',fontsize=18);page.insert_text((921,590),'7',fontsize=18)
            document.save(path)
        info={'homepage':source.repository,'download_status':'downloaded'}
        rows=list(extractors.extract(source,info,self.output));self.assertEqual(len(rows),1)
        self.assertEqual((rows[0].pagina_pdf,rows[0].pagina_impresa),(1,'6–7'))
        self.assertEqual(rows[0].ingredientes,['2 patatas']);self.assertNotIn('Boil',rows[0].elaboracion[0])
        self.assertIn('Boil',rows[0].original_record['full_page_text'])

    def test_public_downloads_are_pinned_cached_and_originals_preserved(self):
        source=sources.SOURCES[0];requests=[]
        def response(request):
            requests.append(request)
            if '/api/datasets/' in str(request.url):return httpx.Response(200,json={'sha':'fixed-revision','siblings':[{'rfilename':'recetas.json'},{'rfilename':'README.md'}]})
            return httpx.Response(200,content=b'[]' if str(request.url).endswith('recetas.json') else b'CC-BY-4.0')
        with httpx.Client(transport=httpx.MockTransport(response)) as session:
            info=sources.discover(source,self.output,session)
            first=sources.download(source,info,self.output,session);count=len(requests)
            second=sources.download(source,info,self.output,session)
        self.assertEqual(first['artifacts'],second['artifacts']);self.assertEqual(len(requests),count)
        self.assertTrue(all(request.headers.get('authorization') is None for request in requests))
        self.assertIn('/fixed-revision/',first['artifacts'][0]['url'])

    def test_download_retries_transient_failures_and_never_publishes_partial_files(self):
        attempts=[];destination=self.output/'recipe.json'
        def response(request):
            attempts.append(request)
            return httpx.Response(503 if len(attempts)==1 else 200,content=b'[]')
        with httpx.Client(transport=httpx.MockTransport(response)) as session,patch.object(sources.time,'sleep'):
            sources._http_file(session,'https://example.test/recipe.json',destination)
        self.assertEqual(len(attempts),2);self.assertEqual(destination.read_bytes(),b'[]')
        with httpx.Client(transport=httpx.MockTransport(lambda request:httpx.Response(200,content=b'too large'))) as session:
            with self.assertRaises(ValueError):sources._http_file(session,'https://example.test/large',self.output/'large.json',limit=2)
        self.assertFalse((self.output/'large.json').exists());self.assertFalse(list(self.output.glob('*.pending')))

    def test_variants_survive_approximate_matching_and_duplicate_provenance_is_kept(self):
        first=fixture('a',source_url_kind='document',source_url='https://example.test/book')
        variant=fixture('b',ingredientes=['100 g de patatas','una cebolla grande'],source_url_kind='document',source_url='https://example.test/book')
        duplicate=copy.deepcopy(first);duplicate['recipe_id']='c';duplicate['source_dataset']='second'
        masters,review,events,_=deduplicate([first,variant,duplicate],{'fixture':0,'second':1})
        self.assertEqual((len(masters),len(review),len(events)),(2,0,1))
        self.assertEqual(len(next(row for row in masters if row['recipe_id']=='a')['provenance']),2)

    def test_stage_rejects_edited_export_and_raw_corruption_never_publishes_stale_source(self):
        source,path=self.prepare()
        pipeline.stage_for_ingestion(self.output)
        before=(self.output/'rag'/'staging.json').read_bytes()
        manifest=json.loads((self.output/'rag'/'manifest.json').read_text(encoding='utf-8'))
        target=self.output/manifest['records'][0]['path'];target.write_text('changed',encoding='utf-8')
        with self.assertRaises(ValueError):pipeline.stage_for_ingestion(self.output)
        self.assertEqual((self.output/'rag'/'staging.json').read_bytes(),before)
        path.write_text('tampered original',encoding='utf-8')
        result=pipeline.stage_sources(self.output,'extract',[source]);self.assertEqual(result[0]['status'],'error')
        self.assertEqual(pipeline.stage_sources(self.output,'normalize',[source])[0]['status'],'unavailable')
        self.assertEqual(pipeline.stage_sources(self.output,'validate',[source])[0]['status'],'unavailable')
        with self.assertRaises(ValueError):pipeline.deduplication(self.output,[source])
        self.assertEqual(path.read_text(encoding='utf-8'),'tampered original')

    def test_obsolete_derived_markdown_is_archived_without_touching_raw(self):
        _,raw=self.prepare();original=raw.read_bytes()
        path=self.output/'rag'/'recipes.jsonl';rows=list(pipeline.records(path))
        pipeline.save_records(path,rows[1:])
        result=pipeline.export(self.output)
        self.assertEqual(result['retired'],1)
        self.assertEqual(len(list((self.output/'markdown').rglob('*.md'))),2)
        self.assertEqual(len(list((self.output/'cache'/'retired-exports').rglob('*.md'))),1)
        self.assertEqual(raw.read_bytes(),original)

    def test_reports_count_duplicate_events_once_even_with_repeated_source_ids(self):
        source,_=self.prepare()
        path=self.output/'validated'/f'{source.id}.jsonl'
        rows=list(pipeline.records(path));pipeline.save_records(path,[*rows,rows[0]])
        pipeline.deduplication(self.output,[source],force=True);pipeline.export(self.output)
        reports(self.output)
        report=(self.output/'reports'/'ingestion_report.md').read_text(encoding='utf-8')
        self.assertIn('| huggingface_es | 4 | 4 | 0 | 1 | 3 |',report)

    def test_private_pilot_reuses_the_generic_engine_without_generation_and_skips_unchanged_files(self):
        self.prepare()
        import rag
        from langchain_core.embeddings import DeterministicFakeEmbedding
        embeddings=DeterministicFakeEmbedding(size=16)
        with patch.object(rag,'generate_ollama') as generate:
            result=verify(self.output,semantic=True,limit=3,embeddings=embeddings)
            self.assertTrue(result['integrity_ok']);self.assertEqual(len(result['questions']),10)
            self.assertFalse(result['production_library_modified'])
            config,_,_,_,_,second=private_index(self.output,3,embeddings=embeddings)
            self.assertTrue(config.db.is_relative_to(self.output))
            self.assertEqual(second['summary']['indexed_files'],0)
            self.assertEqual(second['summary']['skipped_files'],3)
            chunks=rag.open_store(config,embeddings).get(include=['metadatas'])['metadatas']
            self.assertTrue(chunks)
            self.assertTrue(all(item.get('recipe_id') and item.get('source_dataset') and item.get('source_url') for item in chunks))
            generate.assert_not_called()
