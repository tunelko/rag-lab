"""Reports about actual artifacts, formal data quality and license declarations."""
from collections import Counter
import json

from .pipeline import records
from .sources import SOURCES, write_bytes


def reports(output):
    all_rows=[];source_rows={}
    for source in SOURCES:
        path=output/'validated'/f'{source.id}.jsonl'
        rows=list(records(path)) if path.is_file() else []
        all_rows.extend(rows);source_rows[source.id]=rows
    unique=list(records(output/'rag'/'recipes.jsonl')) if (output/'rag'/'recipes.jsonl').is_file() else []
    duplicates=list(records(output/'reports'/'duplicates.jsonl')) if (output/'reports'/'duplicates.jsonl').is_file() else []
    exported=Counter(item['source_dataset'] for item in unique)
    id_to_source={row['recipe_id']:row['source_dataset'] for row in all_rows}
    duplicate_count=Counter(id_to_source[row['recipe_id']] for row in duplicates)
    raw_bytes=0;downloaded=0
    licenses=['# Licencias y procedencia','','Registro de declaraciones de las fuentes, no verificación jurídica de los derechos subyacentes.','',
        '| Dataset | Declaración conservada | Estado y uso |','|---|---|---|']
    source_table=[];errors=[]
    for source in SOURCES:
        rows=source_rows[source.id]
        notices=sorted({row['license_status'] for row in rows})
        detail='No redistribuir el corpus sin revisar los avisos y derechos de todas sus procedencias.'
        if source.id=='huggingface_es':detail='CC-BY-4.0 declarada en README; conservar atribución a belenarbizu y enlace de origen. La ficha no incluye license en sus metadatos.'
        elif source.id=='open_recipe_archive':detail='Unlicense del repositorio y public-domain declarado por receta. Son adaptaciones en inglés: excluidas de la selección española por defecto.'
        elif source.id=='somosnlp':detail='MIT declarada para el dataset; los derechos de las webs originales no quedan verificados por esa etiqueta.'
        elif source.id=='recetas_abuela':detail='Etiqueta OpenRAIL sin identificación suficiente de variante y condiciones completas; restricciones y redistribución pendientes de revisión.'
        elif source.id=='peluso':detail='CC0-1.0 en LICENSE del repositorio; conservar enlaces y atribuciones de terceros contenidos en las notas.'
        elif source.id=='pdf_regionales':detail='Documento público de esmadrid; reutilización y redistribución no verificadas. Se conserva el PDF original.'
        licenses.append('| '+source.id+' | '+('; '.join(notices) or 'No procesada')+' | '+detail+' |')
        discovery=output/'cache'/f'{source.id}-discovery.json'
        if discovery.is_file():
            info=json.loads(discovery.read_text(encoding='utf-8'));revision=info.get('revision') or 'original'
            ledger=output/'raw'/source.id/revision/'download.json'
            if ledger.is_file():
                download=json.loads(ledger.read_text(encoding='utf-8'))
                artifacts=download.get('artifacts',[]);downloaded+=len(artifacts);raw_bytes+=sum(item['bytes'] for item in artifacts)
                if download.get('download_status')!='downloaded':errors.append(source.id+': '+download.get('error','descarga no completada'))
        source_table.append(f"| {source.id} | {len(rows)} | {sum(not row['needs_review'] for row in rows)} | {sum(row['needs_review'] for row in rows)} | {duplicate_count[source.id]} | {exported[source.id]} |")
    countries=Counter(row.get('pais_origen') or 'Origen no confirmado' for row in unique)
    regions=Counter(row['region'] for row in unique if row.get('region'))
    phases={}
    for name in ('extract','normalize','validate'):
        path=output/'cache'/f'{name}.json'
        if path.is_file():phases[name]=sum(row.get('seconds',0) for row in json.loads(path.read_text(encoding='utf-8')))
    index_path=output/'cache'/'index.json'
    indexed=json.loads(index_path.read_text(encoding='utf-8')) if index_path.is_file() else None
    report=['# Informe de preparación e ingesta','','La biblioteca activa de Musa no se modifica. El indexado de comprobación, si se ejecuta, usa una colección privada separada.','',
        f'- Archivos originales conservados: {downloaded}; bytes: {raw_bytes}.',
        f'- Registros originales procesados: {len(all_rows)}.',
        f"- Registros con revisión pendiente: {sum(row['needs_review'] for row in all_rows)}.",
        f'- Duplicados excluidos de la selección final: {len(duplicates)}; originales y eventos conservados.',
        f'- Recetas únicas listas para preparar ingesta: {len(unique)}.',
        '- Tiempos medidos de etapas (segundos): '+json.dumps(phases,ensure_ascii=False)+'.',
        '- Índice de comprobación: '+(json.dumps(indexed.get('summary'),ensure_ascii=False) if indexed else 'No ejecutado')+'.','',
        '| Fuente | Registros | Válidos antes de deduplicar | Revisión | Eventos de duplicado | Únicos exportados |',
        '|---|---:|---:|---:|---:|---:|',*source_table,'','## Países explícitos','',
        *[f'- {name}: {count}.' for name,count in sorted(countries.items())],'','## Regiones documentadas','',
        *[f'- {name}: {count}.' for name,count in sorted(regions.items())],'','## Errores de ejecución','',*(errors or ['Sin errores de descarga registrados.'])]
    last_run=output/'cache'/'last-run.json'
    if last_run.is_file():
        execution=json.loads(last_run.read_text(encoding='utf-8'))
        report.extend(['','## Última ejecución','',f"Comando: {execution['phase']}; tiempo medido: {execution['seconds']} segundos."])
    error_counts=Counter(error for row in all_rows for error in row['errors'])
    quality=['# Calidad del corpus','','Puntuación formal y determinista, no validación culinaria humana ni evaluación nutricional.','',
        '- Título vacío: -35; sin ingredientes: -35; preparación menor de 30 caracteres o 6 palabras: -35.',
        '- Idioma distinto del español: -40; daño de codificación o límites de ingredientes sin resolver: -25.',
        '- Otros errores formales: -20. Mínimo 0, máximo 100. Cualquier error envía el documento a revisión.',
        '- Unidades domésticas e históricas no se convierten a gramos. El número de ingredientes queda desconocido cuando la segmentación solo es heurística.',
        '- País e idioma son propiedades distintas. No se atribuye España por el idioma del texto.',
        '- No se han resumido ni completado instrucciones mediante modelos. Los registros originales y sus campos completos se conservan.','',
        '## Incidencias','',*[f'- {name}: {count}.' for name,count in error_counts.most_common()],
        '','## Muestra de revisión','']
    quality.extend(f"- {row['recipe_id']} · {row['titulo'] or '(sin título)'} · {', '.join(row['errors'])}" for row in [row for row in all_rows if row['needs_review']][:20])
    dedup_path=output/'cache'/'deduplicate.json'
    if dedup_path.is_file():
        dedup=json.loads(dedup_path.read_text(encoding='utf-8'))
        quality.extend(['','## Deduplicación auditable','',
            'Coincidencia exacta por identidad de origen, URL de registro y SHA-256; una URL de documento/libro compartida no implica duplicado.',
            'La fase aproximada usa SequenceMatcher de Python y Jaccard de ingredientes, con cantidades y raciones compatibles. Conserva variantes.','```json',
            json.dumps(dedup['configuration'],ensure_ascii=False,indent=2),'```',
            'Los candidatos que exceden el límite de cada bloque no se comparan aproximadamente; la comprobación exacta sí cubre todos los registros.'])
    verification=output/'reports'/'verification.json'
    if verification.is_file():
        measured=json.loads(verification.read_text(encoding='utf-8'))
        quality.extend(['','## Comprobación de recuperación','',
            'Prueba funcional sobre una muestra y etiquetas derivadas de campos explícitos. No es un benchmark ciego ni mide calidad de respuestas del LLM.',
            'La ausencia de etiquetas no prueba que falten respuestas. En consultas con muchas respuestas posibles, Recall@5 tiene un techo de 5/N; no es una medida de exhaustividad de toda la biblioteca.',
            f"Integridad: {measured.get('integrity_ok')}; originales comprobados: {measured.get('originals_checked',0)}; llamadas de generación: 0.",
            '| Pregunta | Estado | Evidencias en muestra | Recall@5 | MRR@5 | nDCG@5 |',
            '|---|---|---:|---:|---:|---:|'])
        for item in measured.get('questions',[]):
            metrics=item.get('metrics',{})
            quality.append(f"| {item['question']} | {item['status']} | {item['supporting_records_in_pilot']} | {metrics.get('recall@5','—')} | {metrics.get('mrr@5','—')} | {metrics.get('ndcg@5','—')} |")
        if not measured.get('questions'):quality.append('Prueba semántica pendiente; no se han inventado métricas.')
    else:quality.extend(['','## Recuperación','', 'Pendiente de ejecutar; no se han inventado métricas.'])
    for name,lines in [('ingestion_report.md',report),('licenses.md',licenses),('quality_report.md',quality)]:
        write_bytes(output/'reports'/name,('\n'.join(lines)+'\n').encode())
    return {'reports':[str(output/'reports'/name) for name in ('ingestion_report.md','licenses.md','quality_report.md')],
            'original_records':len(all_rows),'unique_recipes':len(unique),'review_records':sum(row['needs_review'] for row in all_rows)}
