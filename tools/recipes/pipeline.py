"""Checkpointed preparation stages, independent from the running document library."""
from contextlib import contextmanager
from functools import lru_cache
import itertools
import inspect
import json
from pathlib import Path
import re
import sqlite3
import time
import uuid

import yaml

from . import extractors
from .models import Recipe, POLICY, canonical_url, json_hash, key, normalize, validate
from .sources import SOURCES, digest, write_bytes, write_json, validate_artifacts


@lru_cache(maxsize=1)
def parser_signature():
    return json_hash([digest(Path(__file__).with_name(name)) for name in ('models.py','extractors.py')])


def log(phase, **values):
    print(json.dumps({'phase':phase, **values}, ensure_ascii=True), flush=True)


def records(path):
    if not Path(path).is_file():return
    with Path(path).open(encoding='utf-8') as stream:
        for line in stream:
            if line.strip():yield json.loads(line)


def save_records(path, values):
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_name(path.name+'.'+uuid.uuid4().hex+'.pending')
    count = 0
    with pending.open('w', encoding='utf-8', newline='\n') as output:
        for value in values:
            output.write(json.dumps(value, ensure_ascii=False, separators=(',', ':'))+'\n');count += 1
    if path.is_file() and digest(path) == digest(pending):pending.unlink()
    else:pending.replace(path)
    return count


def checkpoint(path, signature, operation, force=False):
    metadata = path.with_suffix(path.suffix+'.state.json')
    if not force and path.is_file() and metadata.is_file():
        previous = json.loads(metadata.read_text(encoding='utf-8'))
        if previous.get('signature') == signature and digest(path) == previous.get('sha256'):
            return {**previous, 'reused':True}
    started = time.perf_counter()
    count = save_records(path, operation())
    result = {'signature':signature, 'sha256':digest(path), 'records':count,
              'seconds':round(time.perf_counter()-started,3)}
    write_json(metadata,result)
    return result


def download_info(output, source):
    discovery = json.loads((output/'cache'/f'{source.id}-discovery.json').read_text(encoding='utf-8'))
    path = output/'raw'/source.id/(discovery.get('revision') or 'original')/'download.json'
    return json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {**discovery,'download_status':'unavailable'}


def stage_sources(output, phase, selected, limit=None, force=False):
    summary = []
    for source in selected:
        log(phase, source=source.id, status='starting')
        try:
            if phase == 'extract':
                info = download_info(output,source)
                if info.get('download_status') != 'downloaded':
                    summary.append({'source':source.id,'status':'unavailable','error':info.get('error')});continue
                validate_artifacts(output,info)
                path = extractors.main_path(source,info,output)
                inputs = sorted(path.rglob('*.md')) if path.is_dir() and source.id=='peluso' else [path/'recipes.jsonl' if path.is_dir() else path]
                signature = json_hash([POLICY,parser_signature(),limit,[(item.relative_to(output).as_posix(),digest(item)) for item in inputs]])
                operation = lambda: (recipe.model_dump(mode='json') for recipe in itertools.islice(extractors.extract(source,info,output),limit))
                target = output/'extracted'/f'{source.id}.jsonl'
            else:
                previous_phase = 'extracted' if phase=='normalize' else 'normalized'
                previous_cache=output/'cache'/('extract.json' if phase=='normalize' else 'normalize.json')
                states={item['source']:item for item in json.loads(previous_cache.read_text(encoding='utf-8'))} if previous_cache.is_file() else {}
                if source.id in states and states[source.id]['status']!='done':
                    summary.append({'source':source.id,'status':'unavailable','error':'Previous stage did not publish this source.'});continue
                path = output/previous_phase/f'{source.id}.jsonl'
                if not path.is_file():
                    summary.append({'source':source.id,'status':'unavailable','error':'Previous stage is absent.'});continue
                signature = json_hash([POLICY,parser_signature(),limit,digest(path)])
                transform = normalize if phase=='normalize' else validate
                operation = lambda: (transform(Recipe.model_validate(item)).model_dump(mode='json') for item in itertools.islice(records(path),limit))
                target = output/('normalized' if phase=='normalize' else 'validated')/f'{source.id}.jsonl'
            result = checkpoint(target,signature,operation,force)
            result.update(source=source.id,status='done');summary.append(result)
            log(phase,source=source.id,records=result['records'],reused=result.get('reused',False),status='done')
        except Exception as error:
            result={'source':source.id,'status':'error','error':str(error)};summary.append(result);log(phase,**result)
    cache_path=output/'cache'/f'{phase}.json'
    previous={item['source']:item for item in json.loads(cache_path.read_text(encoding='utf-8'))} if cache_path.is_file() else {}
    previous.update({item['source']:item for item in summary})
    write_json(cache_path,list(previous.values()))
    return summary


@contextmanager
def database(output):
    path = output/'rag'/'catalog.sqlite3';path.parent.mkdir(parents=True,exist_ok=True)
    connection=sqlite3.connect(path);connection.row_factory=sqlite3.Row
    try:
        with connection:
            connection.executescript('''
                CREATE TABLE IF NOT EXISTS recipes (
                    recipe_id TEXT PRIMARY KEY, source_dataset TEXT NOT NULL, title TEXT NOT NULL,
                    country TEXT, region TEXT, language TEXT, historic INTEGER NOT NULL,
                    ingredient_count INTEGER, servings INTEGER, content_hash TEXT NOT NULL,
                    canonical_url TEXT, needs_review INTEGER NOT NULL, duplicate_of TEXT, data TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1);
                CREATE INDEX IF NOT EXISTS recipe_country ON recipes(country);
                CREATE INDEX IF NOT EXISTS recipe_servings ON recipes(servings);
                CREATE INDEX IF NOT EXISTS recipe_hash ON recipes(content_hash);
            ''')
            if 'active' not in {row['name'] for row in connection.execute('PRAGMA table_info(recipes)')}:
                connection.execute('ALTER TABLE recipes ADD COLUMN active INTEGER NOT NULL DEFAULT 1')
            yield connection
    finally:connection.close()


def _bucket(recipe):
    if recipe.get('needs_review'):return 'revision'
    if recipe.get('es_historica'):return 'historicas'
    if not recipe.get('pais_origen'):return 'origen_no_confirmado'
    return 'espana' if recipe.get('pais_origen')=='España' else 'internacional'


def markdown(recipe):
    # The original full record remains in normalized JSONL; Markdown carries the
    # original culinary text plus concise provenance and optional typed fields.
    fields = ('recipe_id','titulo','pais_origen','region','categoria','raciones','tiempo_preparacion_min',
              'tiempo_coccion_min','tiempo_total_min','source_dataset','source_url','source_file','source_record',
              'license_status','idioma','es_historica','autor','libro','anio_publicacion','pagina_pdf','pagina_impresa',
              'establecimiento','ingredient_count','needs_review','quality_score')
    metadata={name:recipe.get(name) for name in fields}
    metadata['source_attribution']=recipe.get('source_attribution',{})
    attributions={json.dumps({name:item.get(name) for name in ('source_dataset','source_url','license_status')},sort_keys=True)
                  for item in recipe.get('provenance',[])}
    metadata['attributions']=[json.loads(item) for item in sorted(attributions)]
    front = yaml.safe_dump(metadata,allow_unicode=True,sort_keys=False).strip()
    body=['---',front,'---','', '# '+(recipe['titulo'] or 'Registro sin título'),'', '## Ingredientes','']
    body.extend('- '+item.replace('\n','\n  ') for item in recipe.get('ingredientes',[]))
    body.extend(['','## Elaboración',''])
    for index,step in enumerate(recipe.get('elaboracion',[]),1):
        body.append(f'{index}. '+re.sub(r'^\d+[.)]\s+', '',step).replace('\n','\n   '))
    if recipe.get('additional_text'):body.extend(['','## Información adicional','',recipe['additional_text']])
    if recipe.get('needs_review'):
        body.extend(['','## Revisión pendiente','',', '.join(recipe.get('errors',[]))])
        if not recipe.get('elaboracion') and recipe.get('original_text'):
            body.extend(['','## Contenido original para revisar','',recipe['original_text']])
    return '\n'.join(body)+'\n'


def deduplication(output, selected, force=False, **thresholds):
    from .deduplicate import deduplicate
    from .sources import SOURCES
    states_path=output/'cache'/'validate.json'
    states={item['source']:item['status'] for item in json.loads(states_path.read_text(encoding='utf-8'))} if states_path.is_file() else {}
    inputs=[path for source in SOURCES if (path:=output/'validated'/f'{source.id}.jsonl').is_file() and states.get(source.id,'done')=='done']
    if not inputs:raise ValueError('No sources have a completed validation stage.')
    signature=json_hash([POLICY,digest(Path(__file__).with_name('deduplicate.py')),thresholds,[(path.name,digest(path)) for path in inputs]])
    cache=output/'cache'/'deduplicate.json'
    canonical=output/'rag'/'recipes.jsonl';review=output/'rag'/'review.jsonl';events=output/'reports'/'duplicates.jsonl'
    if not force and cache.is_file() and canonical.is_file() and review.is_file() and events.is_file():
        previous=json.loads(cache.read_text(encoding='utf-8'))
        if (previous.get('signature')==signature and previous.get('canonical_sha256')==digest(canonical)
            and previous.get('review_sha256')==digest(review) and previous.get('duplicates_sha256')==digest(events)
            and (output/'rag'/'catalog.sqlite3').is_file()):
            return {**previous,'reused':True}
    rows=[row for path in inputs for row in records(path)]
    masters,quarantine,duplicates,configuration=deduplicate(rows,{item.id:index for index,item in enumerate(SOURCES)},
        observer=lambda processed,total,unique:log('deduplicate',processed=processed,total=total,unique=unique,status='running'),**thresholds)
    save_records(canonical,masters);save_records(review,quarantine);save_records(events,duplicates)
    with database(output) as connection:
        connection.execute('UPDATE recipes SET active=0 WHERE active=1')
        for recipe in masters+quarantine:
            connection.execute('''INSERT INTO recipes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)
                ON CONFLICT(recipe_id) DO UPDATE SET source_dataset=excluded.source_dataset,title=excluded.title,
                country=excluded.country,region=excluded.region,language=excluded.language,historic=excluded.historic,
                ingredient_count=excluded.ingredient_count,servings=excluded.servings,content_hash=excluded.content_hash,
                canonical_url=excluded.canonical_url,needs_review=excluded.needs_review,duplicate_of=NULL,data=excluded.data,active=1''',
                (recipe['recipe_id'],recipe['source_dataset'],recipe['titulo'],recipe.get('pais_origen'),recipe.get('region'),
                 recipe['idioma'],int(recipe['es_historica']),recipe.get('ingredient_count'),recipe.get('raciones'),
                 recipe['content_hash'],canonical_url(recipe['source_url']),int(recipe['needs_review']),None,json.dumps(recipe,ensure_ascii=False)))
    result={'signature':signature,'input_records':len(rows),'valid_records':len(rows)-len(quarantine),
        'unique_recipes':len(masters),'review_records':len(quarantine),'duplicate_records':len(duplicates),
        'canonical_sha256':digest(canonical),'review_sha256':digest(review),'duplicates_sha256':digest(events),'configuration':configuration}
    write_json(cache,result);return result


def export(output, force=False):
    from .sources import write_bytes
    cache=output/'cache'/'export.json';manifest_path=output/'rag'/'manifest.json'
    ingestion_path=output/'rag'/'ingestion.jsonl'
    signature=json_hash(['export-ingestion-v2',digest(output/'rag'/'recipes.jsonl'),digest(output/'rag'/'review.jsonl'),inspect.getsource(markdown),inspect.getsource(_bucket)])
    if not force and cache.is_file() and manifest_path.is_file():
        previous=json.loads(cache.read_text(encoding='utf-8'))
        if previous.get('signature')==signature and previous.get('manifest_sha256')==digest(manifest_path) and ingestion_path.is_file() and previous.get('ingestion_sha256')==digest(ingestion_path):
            return {**previous,'written':0,'reused':previous['documents']}
    manifests=[];written=0
    for filename in ('recipes.jsonl','review.jsonl'):
        for recipe in records(output/'rag'/filename):
            slug=re.sub(r'[^a-z0-9]+','-',key(recipe['titulo'])).strip('-')[:70] or 'registro'
            suffix='_'+json_hash([recipe['original_hash'],recipe['source_record']])[:12] if recipe['needs_review'] else ''
            path=Path('markdown')/_bucket(recipe)/(slug+'_'+recipe['recipe_id']+suffix+'.md')
            payload=markdown(recipe).encode()
            written+=int(write_bytes(output/path,payload))
            manifests.append({'recipe_id':recipe['recipe_id'],'path':path.as_posix(),'sha256':digest(output/path),
                'source_dataset':recipe['source_dataset'],'needs_review':recipe['needs_review'],
                'provenance':recipe.get('provenance',[])})
            if len(manifests)%2000==0:log('export',status='running',documents=len(manifests),written=written)
    # Only these published paths are eligible for staging; old derived files and
    # review records are never swept into the production library by globbing.
    manifest={'policy':POLICY,'records':manifests}
    paths={item['recipe_id']:item['path'] for item in manifests if not item['needs_review']}
    save_records(ingestion_path,({**recipe,'markdown_path':paths[recipe['recipe_id']],'embedding_text':markdown(recipe)}
                                for recipe in records(output/'rag'/'recipes.jsonl')))
    write_json(manifest_path,manifest)
    current={item['path'] for item in manifests};retired=[]
    for path in (output/'markdown').rglob('*.md'):
        relative=path.relative_to(output).as_posix()
        if relative in current or not re.search(r'_recipe_[0-9a-f]{24}(?:_[0-9a-f]{12})?\.md$',path.name):continue
        if not path.resolve().is_relative_to((output/'markdown').resolve()):raise ValueError('An obsolete export escapes markdown/.')
        target=output/'cache'/'retired-exports'/digest(path)/path.name
        if target.exists():target=target.with_name(target.stem+'_'+uuid.uuid4().hex+target.suffix)
        if not target.resolve().is_relative_to(output.resolve()):raise ValueError('Invalid retirement path.')
        target.parent.mkdir(parents=True,exist_ok=True);path.rename(target)
        retired.append({'from':relative,'to':target.relative_to(output).as_posix()})
    if retired:save_records(output/'reports'/'retired-exports.jsonl',retired)
    result={'documents':len(manifests),'written':written,'reused':len(manifests)-written,
            'ready':sum(not item['needs_review'] for item in manifests),'retired':len(retired)}
    result.update(signature=signature,manifest_sha256=digest(manifest_path),ingestion_sha256=digest(ingestion_path))
    write_json(cache,result);return result


def stage_for_ingestion(output, limit=None):
    manifest=json.loads((output/'rag'/'manifest.json').read_text(encoding='utf-8'))
    selected=[item for item in manifest['records'] if not item['needs_review']]
    if limit is not None:selected=selected[:limit]
    for item in selected:
        path=(output/item['path']).resolve()
        if not path.is_relative_to(output.resolve()) or digest(path)!=item['sha256']:
            raise ValueError('A staged export is missing or has changed: '+item['recipe_id'])
    result={'policy':POLICY,'documents':selected,'production_library_modified':False}
    write_json(output/'rag'/'staging.json',result)
    return {'documents':len(selected),'production_library_modified':False}
