"""Opt-in checks on a private collection, reusing Musa's unchanged retrieval code."""
from collections import Counter
from contextlib import redirect_stdout
from dataclasses import replace
import json
import os
from pathlib import Path
import time

import yaml

from .models import canonical_url, json_hash, key
from .pipeline import records, save_records
from .sources import digest, write_json, validate_artifacts


QUESTIONS = (
    '¿Cómo preparo una fabada asturiana?',
    '¿Qué puedo cocinar con patatas, huevos y cebolla?',
    'Dame recetas tradicionales de Asturias.',
    '¿Qué platos de cuchara españoles llevan garbanzos?',
    'Busca recetas con menos de cinco ingredientes.',
    '¿Cómo se elabora un cocido madrileño?',
    '¿Qué recetas de pescado hay sin horno?',
    'Busca recetas históricas españolas del siglo XIX.',
    'Dame recetas mediterráneas sin carne.',
    '¿Qué recetas puedo preparar para dos personas?',
)


def read_export(path):
    """Keep source metadata on every chunk through the existing loader hook."""
    from langchain_core.documents import Document

    text=path.read_text(encoding='utf-8')
    if not text.startswith('---\n'):raise ValueError('The recipe export has no metadata.')
    metadata,separator,_=text[4:].partition('\n---\n')
    if not separator:raise ValueError('The recipe export has unterminated metadata.')
    front=yaml.safe_load(metadata)
    if not isinstance(front,dict) or not front.get('recipe_id') or front.get('needs_review'):
        raise ValueError('Only valid, attributed recipe exports may be indexed.')
    return [Document(page_content=text,metadata={**front,'page':0,'item_type':'text'})]


def judgments(recipe):
    title=key(recipe['titulo']);names=' '.join(item['ingrediente'] for item in recipe['ingredientes_normalizados'])
    diets={key(item) for item in recipe.get('tipo_dieta',[])}
    fish=any(word in title for word in ('pescado','merluza','bacalao','salmon','sardina','dorada','lubina'))
    return (
        'fabada' in title and recipe.get('pais_origen')=='España',
        all(word in names for word in ('patata','huevo','cebolla')),
        recipe.get('region')=='Asturias',
        recipe.get('pais_origen')=='España' and 'garbanzo' in names and any(word in title for word in ('potaje','cocido','guiso','cuchara')),
        recipe.get('ingredient_count') is not None and recipe['ingredient_count']<5,
        'cocido madrileno' in title,
        fish and 'sin horno' in title,
        recipe['idioma']=='es' and recipe.get('es_historica') and 1800<=int(recipe.get('anio_publicacion') or 0)<=1899,
        'mediterr' in title and (bool(diets & {'vegetariana','vegetariano','vegana','vegano','vegan','vegetarian'}) or 'sin carne' in title),
        recipe.get('raciones')==2 and not any(item.get('field')=='raciones' for item in recipe.get('metadata_conflicts',[])),
    )


def sample(output, limit):
    recipes=list(records(output/'rag'/'recipes.jsonl'))
    selected={};labels=[[] for _ in QUESTIONS]
    for recipe in recipes:
        for index,relevant in enumerate(judgments(recipe)):
            if relevant:labels[index].append(recipe['recipe_id'])
    by_id={row['recipe_id']:row for row in recipes}
    for index in range(3):
        for group in labels:
            if index<len(group) and len(selected)<limit:selected[group[index]]=by_id[group[index]]
    priority=sorted(recipes,key=lambda row:(row['source_dataset']!='huggingface_es',row['pais_origen']!='España',row['recipe_id']))
    for recipe in priority:
        if len(selected)>=limit:break
        selected.setdefault(recipe['recipe_id'],recipe)
    manifest=json.loads((output/'rag'/'manifest.json').read_text(encoding='utf-8'))
    paths={item['recipe_id']:item['path'] for item in manifest['records'] if not item['needs_review']}
    return selected,labels,paths


def private_index(output, limit=160, *, embeddings=None):
    # This changes only this command process, never the running server or Ollama.
    os.environ['CUDA_VISIBLE_DEVICES']=''
    os.environ.setdefault('OMP_NUM_THREADS','2');os.environ.setdefault('MKL_NUM_THREADS','2')
    import rag
    selected,labels,paths=sample(output,limit)
    if not selected:raise ValueError('There are no valid exported recipes for the private pilot.')
    config=replace(rag.Config(),db=output/'rag'/'verification-index',collection='recipes_verification',embedding_device='cpu')
    if embeddings is None:embeddings=rag.make_embeddings(config,allow_download=False)
    chosen=[(output/paths[identifier]).resolve() for identifier in selected]
    for path in chosen:
        if not path.is_relative_to((output/'markdown').resolve()):raise ValueError('Invalid exported document path.')
    started=time.perf_counter();log=output/'reports'/'private-index.log';log.parent.mkdir(parents=True,exist_ok=True)
    with log.open('a',encoding='utf-8') as target,redirect_stdout(target):
        summary=rag.ingest(config,output/'markdown',embeddings,paths=chosen,loader=read_export,
                           policy_for=lambda path:'recipe-adapter:frontmatter-v1')
    if summary['errors']:raise ValueError('Private verification indexing failed; see '+str(log))
    result={'summary':summary,'documents_selected':len(selected),'embedding_model':config.embedding_model,
            'device':'cpu','seconds':round(time.perf_counter()-started,3),'production_library_modified':False}
    write_json(output/'cache'/'index.json',result)
    return config,embeddings,selected,labels,paths,result


def verify(output, *, semantic=False, limit=160, embeddings=None):
    rows=list(records(output/'rag'/'recipes.jsonl'))
    ids=Counter(row['recipe_id'] for row in rows);hashes=Counter(row['content_hash'] for row in rows)
    urls=Counter(canonical_url(row['source_url']) for row in rows if row.get('source_url_kind')=='record')
    manifest=json.loads((output/'rag'/'manifest.json').read_text(encoding='utf-8'))
    failures=[]
    export_cache=json.loads((output/'cache'/'export.json').read_text(encoding='utf-8'))
    ingestion_path=output/'rag'/'ingestion.jsonl'
    if not ingestion_path.is_file() or digest(ingestion_path)!=export_cache.get('ingestion_sha256'):
        failures.append({'error':'ingestion_jsonl_checksum'})
    originals=0
    for ledger in (output/'raw').glob('*/*/download.json'):
        info=json.loads(ledger.read_text(encoding='utf-8'))
        if info.get('download_status')=='downloaded':
            try:originals+=validate_artifacts(output,info)
            except (OSError,ValueError) as error:failures.append({'error':'raw_checksum','detail':str(error)})
    for row in manifest['records']:
        path=(output/row['path']).resolve()
        if not path.is_relative_to(output.resolve()) or not path.is_file() or digest(path)!=row['sha256']:
            failures.append({'recipe_id':row['recipe_id'],'error':'export_hash'})
    for row in rows:
        if json_hash(row['original_record'])!=row['original_hash']:failures.append({'recipe_id':row['recipe_id'],'error':'original_record_hash'})
    for kind,counts in [('recipe_id',ids),('content_hash',hashes),('canonical_url',urls)]:
        if any(count>1 for count in counts.values()):failures.append({'error':'duplicate_'+kind})
    result={'integrity_ok':not failures,'originals_checked':originals,'unique_recipes':len(rows),'exports_checked':len(manifest['records']),
            'failures':failures,'production_library_modified':False,'generation_calls':0}
    if failures:
        write_json(output/'reports'/'verification.json',result);return result
    if semantic:
        import retrieval_pipeline
        from evaluation import score_ranking
        config,embeddings,selected,labels,paths,index=private_index(output,limit,embeddings=embeddings)
        allowed=frozenset(Path(paths[identifier]).relative_to('markdown').as_posix() for identifier in selected)
        questions=[];lab=[]
        for index_number,question in enumerate(QUESTIONS):
            expected=[identifier for identifier in labels[index_number] if identifier in selected]
            detail={'id':str(index_number+1),'question':question,'supporting_records_in_corpus':len(labels[index_number]),
                    'supporting_records_in_pilot':len(expected)}
            if not expected:
                detail['status']='not_evaluable_without_reference_labels';questions.append(detail);continue
            started=time.perf_counter()
            hits,trace=retrieval_pipeline.retrieve(config,question,embeddings,8,files=allowed,reranker=None)
            qrels=[{'file':Path(paths[identifier]).relative_to('markdown').as_posix(),'relevance':1} for identifier in expected]
            raw=[{'metadata':dict(document.metadata),'text':document.page_content} for document,_ in hits]
            detail.update(status='measured',metrics=score_ranking(raw,qrels,ks=(5,8)),seconds=round(time.perf_counter()-started,4),
                retrieved=[{'file':doc.metadata['file'],'distance':float(distance)} for doc,distance in hits])
            questions.append(detail)
            # Compatible cases can be imported into the existing generic Laboratory;
            # this is a pilot with explicit field-derived labels, not a blind benchmark.
            if len(qrels)<=20:lab.append({'id':detail['id'],'question':question,'relevant':qrels})
        result.update(index=index,questions=questions,
            evaluation_scope='Private pilot, production focused retrieval without optional reranker; labels derived from explicit recipe fields, not a human blind benchmark.',
            labeling_note='Supporting-record counts reflect these explicit labeling rules only. Zero labels do not establish that the question is unanswerable. Generation and abstention were not tested.')
        save_records(output/'rag'/'laboratory-pilot.jsonl',lab)
    write_json(output/'reports'/'verification.json',result)
    return result
