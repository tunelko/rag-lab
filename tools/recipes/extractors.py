"""Deterministic source adapters; original records remain intact alongside parsed fields."""
import ast
import csv
import json
from pathlib import Path
import re

import yaml

from .models import Recipe, clean_text, key, stable_id


def list_field(value, *, commas=False):
    if isinstance(value, list):
        return [clean_text(item) for item in value if clean_text(item)], 'list'
    text = clean_text(value)
    if not text:
        return [], 'list'
    if text.startswith('['):
        try:
            if len(text)>65536:return [text], 'unsplit'
            parsed = ast.literal_eval(text)
            if isinstance(parsed, (list, tuple)):
                return list_field(list(parsed))
        except (ValueError, SyntaxError, RecursionError):
            return [text], 'unsplit'
        return [text], 'unsplit'
    if '\n' in text:
        return [re.sub(r'^\s*[-•*]\s*', '', item).strip() for item in text.splitlines() if item.strip()], 'lines'
    blocks=[item.strip() for item in re.split(r'[ \t]{4,}',text) if item.strip()]
    if len(blocks)>1 and all(re.search(r'[a-zA-ZáéíóúñÁÉÍÓÚÑ]',item) for item in blocks):
        return blocks, 'space-blocks'
    if commas:
        parts, start, depth = [], 0, 0
        for position, char in enumerate(text):
            if char in '([{':depth += 1
            elif char in ')]}':depth = max(0, depth - 1)
            elif char == ',' and not depth and not (position and position+1 < len(text) and text[position-1].isdigit() and text[position+1].isdigit()):
                parts.append(text[start:position].strip());start = position + 1
        parts.append(text[start:].strip())
        merged = []
        for part in parts:
            if merged and re.match(r'^(picad|rallad|trocead|cortad|tamizad|opcional|preferiblemente)', key(part)):
                merged[-1] += ', ' + part
            elif part:merged.append(part)
        return merged, 'comma-heuristic'
    # A single unbroken ingredient is valid, but a long quantity-rich blob is not
    # silently reported as a single ingredient for numeric filtering.
    boundary = 'unsplit' if len(text) > 180 and len(re.findall(r'\b\d+\s+\w+', text)) > 2 else 'unstructured-text'
    return [text], boundary


def steps_field(value):
    if isinstance(value, list):return [clean_text(item) for item in value if clean_text(item)]
    text = clean_text(value)
    if not text:return []
    lines = text.splitlines() if '\n' in text else re.split(r'\s+(?=\d+[.)]\s+)', text)
    lines = [line for line in lines if line.strip()]
    if len(lines) > 1 and all(re.match(r'^' + str(index) + r'[.)]\s+', line.strip()) for index, line in enumerate(lines, 1)):
        return [re.sub(r'^\d+[.)]\s+', '', line.strip()) for line in lines]
    return [line.strip() for line in lines if line.strip()]


def instruction_language(steps):
    words=re.findall(r'\b\w+\b',key(' '.join(steps)))
    english={'the','with','and','add','for','then','until','into','cook','heat','let','boil','chop','serve','mix','stir','cut'}
    spanish={'de','el','la','los','las','con','para','hasta','anadir','cocer','hervir','freir','servir','mezclar','picar'}
    en=sum(word in english for word in words);es=sum(word in spanish for word in words)
    if en>=3 and en>2*es:return 'en'
    if es>=3 and es>2*en:return 'es'
    return 'und'


def integer(value):
    if isinstance(value, int) and not isinstance(value, bool):return value if value > 0 else None
    match = re.fullmatch(r'(?:para\s+)?(\d+)\s*(?:personas?|raciones?|comensales?)?', str(value or '').strip(), re.I)
    return int(match[1]) if match and int(match[1]) > 0 else None


def duration(value):
    text = key(value or '')
    hours = re.fullmatch(r'(\d+):(\d\d)', text)
    if hours:return int(hours[1])*60+int(hours[2])
    minutes = re.fullmatch(r'(\d+)\s*(?:min|minutos?)?', text)
    return int(minutes[1]) if minutes else None


def csv_rows(path):
    with path.open('rb') as source:sample = source.read(65536)
    if sample.startswith((b'\xff\xfe', b'\xfe\xff')):encoding = 'utf-16'
    else:
        try:sample.decode('utf-8-sig');encoding = 'utf-8-sig'
        except UnicodeDecodeError:encoding = 'cp1252'
    with path.open(encoding=encoding, newline='') as source:
        sample = source.read(65536);source.seek(0)
        dialect = csv.Sniffer().sniff(sample, delimiters=',;\t|')
        csv.field_size_limit(8*1024**2)
        for index, row in enumerate(csv.DictReader(source, dialect=dialect), 1):
            yield index, row, encoding, dialect.delimiter


def sections(markdown):
    result, heading = {}, 'intro'
    for line in markdown.splitlines():
        match = re.match(r'^#{1,6}\s+(.+?)\s*#*$', line)
        if match:
            heading = key(match[1]);result.setdefault(heading, [])
        else:
            result.setdefault(heading, []).append(line)
    return {name: '\n'.join(lines).strip() for name, lines in result.items()}


def markdown_fields(text):
    match = re.match(r'^\ufeff?---\s*\n(.*?)\n---\s*(?:\n|$)', text, re.S)
    front = yaml.safe_load(match[1]) if match else {}
    front = front if isinstance(front, dict) else {}
    body = text[match.end():] if match else text
    parts = sections(body)
    ingredients = next((value for name, value in parts.items() if name in {'ingredientes', 'ingredients'}), '')
    directions = next((value for name, value in parts.items() if name in {'elaboracion', 'preparacion', 'instrucciones', 'directions', 'instructions'}), '')
    items, _ = list_field(ingredients)
    items = [re.sub(r'^[-*+]\s+', '', item) for item in items]
    notes = '\n\n'.join(value for name, value in parts.items() if name not in
                        {'ingredientes', 'ingredients', 'elaboracion', 'preparacion', 'instrucciones', 'directions', 'instructions'})
    notes = re.sub(r'!\[[^\]]*\]\([^)]*\)', '', notes).strip()
    return front, body, items, steps_field(directions), notes


def main_path(source, info, output):
    root = output / 'raw' / source.id / (info.get('revision') or 'original')
    return root / 'repository' / source.collection if source.kind == 'github' else root / source.filename


def license_status(source, path):
    if source.id == 'huggingface_es':
        readme = path.parent / 'README.md'
        text = readme.read_text(encoding='utf-8') if readme.is_file() else ''
        return 'CC-BY-4.0_declarada_en_README' if re.search(r'CC.BY.4.0', text, re.I) else 'pendiente_verificacion'
    return {'somosnlp': 'MIT_dataset_derechos_web_originales_no_verificados',
            'recetas_abuela': 'OpenRAIL_declarada_variante_y_restricciones_no_verificadas',
            'open_recipe_archive': 'public-domain_declarado_recetas_Unlicense_repositorio',
            'peluso': 'CC0-1.0_declarada', 'pdf_regionales': 'derechos_no_verificados'}[source.id]


def base_recipe(source, info, output, path, record, identifier, title, ingredients, directions, url=None, **extra):
    homepage = info.get('homepage') or source.repository
    url_kind = extra.pop('source_url_kind', 'record' if url else 'dataset')
    return Recipe(recipe_id=stable_id(source.id, identifier, url, title), titulo=clean_text(title),
        ingredientes=ingredients, elaboracion=directions, source_dataset=source.id,
        source_url=str(url or homepage), source_url_kind=url_kind,
        source_attribution={'dataset_url':homepage,'maintainer':source.repository.split('/')[0] if source.kind in {'hf','github'} else 'esmadrid.com',
                            'revision':info.get('revision')}, source_file=path.relative_to(output).as_posix(),
        source_record=str(record), license_status=license_status(source, path), **extra)


def extract(source, info, output):
    if info.get('download_status') != 'downloaded':return
    path = main_path(source, info, output)
    if source.id == 'huggingface_es':
        value = json.loads(path.read_text(encoding='utf-8-sig'))
        rows = value if isinstance(value, list) else value.get('recetas', value.get('recipes', []))
        for index, row in enumerate(rows, 1):
            ingredients, method = list_field(row.get('ingredientes'))
            title = row.get('nombre', row.get('titulo', row.get('title', '')))
            recipe = base_recipe(source, info, output, path, index, row.get('id'), title, ingredients,
                steps_field(row.get('instrucciones', row.get('elaboracion'))), original_record=row,
                original_text=json.dumps(row, ensure_ascii=False), pais_origen='España', pais_iso='ESP',
                ingredient_segmentation=method, extraction_method='json-fields-v1',
                raciones=integer(row.get('porciones')), tiempo_total_min=duration(row.get('tiempo_minutos')),
                categoria_original=row.get('categoria'), categoria=key(row.get('categoria', '')).replace(' ', '_') or None,
                dificultad=row.get('dificultad'), tipo_dieta=row.get('dieta') or [],
                valor_nutricional={'calorias_aprox': row['calorias_aprox']} if row.get('calorias_aprox') is not None else None)
            yield recipe
    elif source.kind == 'hf':
        for index, row, encoding, delimiter in csv_rows(path):
            abuela = source.id == 'recetas_abuela'
            ingredients, method = list_field(row.get('Ingredientes' if abuela else 'ingredients'), commas=abuela)
            title = row.get('Nombre' if abuela else 'title', '')
            url = row.get('URL' if abuela else 'url')
            recipe = base_recipe(source, info, output, path, index, row.get('Id' if abuela else 'uuid'),
                title, ingredients, steps_field(row.get('Pasos' if abuela else 'steps')), url,
                original_record={**row, '_csv_encoding':encoding, '_csv_delimiter':delimiter},
                original_text=json.dumps(row, ensure_ascii=False), ingredient_segmentation=method,
                extraction_method='csv-fields-v1', pais_original=row.get('Pais'),
                raciones=integer(row.get('Comensales')), tiempo_total_min=duration(row.get('Duracion')),
                categoria_original=row.get('Categoria'), categoria=key(row.get('Categoria', '')).replace(' ', '_') or None,
                dificultad=row.get('Dificultad') or None, valor_nutricional=row.get('Valor nutricional') or None,
                additional_text=clean_text(row.get('Contexto')))
            if method == 'comma-heuristic':recipe.warnings.append('ingredient_count_unknown_comma_segmentation')
            yield recipe
    elif source.id == 'open_recipe_archive':
        with (path/'recipes.jsonl').open(encoding='utf-8') as stream:
            for index, line in enumerate(stream, 1):
                row = json.loads(line);body = row.get('body', '')
                _, _, ingredients, directions, notes = markdown_fields(body)
                language = instruction_language(directions)
                yield base_recipe(source, info, output, path/'recipes.jsonl', index, row.get('slug'), row.get('title', ''),
                    ingredients, directions, row.get('source_url'), original_record=row, original_text=body,
                    ingredient_segmentation='markdown-list', extraction_method='jsonl-markdown-v1',
                    pais_origen='España' if row.get('culture')=='spain' else None, pais_iso='ESP' if row.get('culture')=='spain' else None,
                    autor=row.get('author'), libro=row.get('source_title'), anio_publicacion=integer(row.get('source_year')),
                    es_historica=True, idioma=language, additional_text=notes, source_url_kind='document')
    elif source.id == 'peluso':
        for file in sorted(path.rglob('*.md')):
            text = file.read_text(encoding='utf-8-sig')
            front, body, ingredients, directions, notes = markdown_fields(text)
            title_match = re.search(r'^#\s+(.+)', body, re.M)
            title = title_match[1] if title_match else file.stem
            relative = file.relative_to(path.parent).as_posix()
            url = f'https://github.com/{source.repository}/blob/{info["revision"]}/{relative}'
            original = {'frontmatter': json.loads(json.dumps(front, default=str)), 'markdown': text}
            recipe = base_recipe(source, info, output, file, relative, relative, title, ingredients, directions, url,
                original_record=original, original_text=text, ingredient_segmentation='markdown-list',
                extraction_method='obsidian-markdown-v1', raciones=integer(front.get('raciones')),
                tiempo_total_min=duration(front.get('tiempo_elaboracion')), dificultad=front.get('dificultad'),
                alergenos=front.get('alergenos') or [], tipo_dieta=[item for item in front.get('etiquetas', []) if key(item) in {'vegan','vegano','vegetariano'}],
                valor_nutricional=front.get('info_nutricional'), additional_text=clean_text(notes))
            if any(word in key(file.stem) for word in ('plantilla', 'template')):recipe.errors.append('template_not_finished_recipe')
            yield recipe
    else:
        yield from madrid_pdf(source, info, output, path)


def madrid_pdf(source, info, output, path):
    import pymupdf
    with pymupdf.open(path) as document:
        for index, page in enumerate(document):
            width, height = page.rect.width, page.rect.height
            lines = []
            for block in page.get_text('dict', flags=pymupdf.TEXTFLAGS_TEXT)['blocks']:
                for line in block.get('lines', []):
                    spans = line.get('spans', [])
                    if not spans:continue
                    lines.append({'text': ''.join(span['text'] for span in spans), 'bbox':list(line['bbox']),
                                  'size':max(span['size'] for span in spans)})
            title = [line for line in lines if .15*width < line['bbox'][0] < .45*width and 25 <= line['size'] <= 40]
            preparation = [line for line in lines if .51*width < line['bbox'][0] < .6*width and
                           line['bbox'][1] > .38*height and 9.5 <= line['size'] <= 12]
            ingredient_lines = [line for line in lines if .16*width < line['bbox'][0] < .30*width and
                                line['bbox'][1] > .57*height and 9.5 <= line['size'] <= 12]
            if not title or not preparation or not ingredient_lines:continue
            ingredients = []
            for line in sorted(ingredient_lines, key=lambda item:item['bbox'][1]):
                text = line['text'].strip()
                if ingredients and text and text[0].islower():ingredients[-1] += ' ' + text
                elif text:ingredients.append(text)
            title_text = ' '.join(line['text'] for line in sorted(title, key=lambda item:item['bbox'][1]))
            directions = ' '.join(line['text'].strip() for line in sorted(preparation,key=lambda item:item['bbox'][1]))
            restaurant = [line['text'] for line in lines if line['bbox'][0] > width/2 and 25 <= line['size'] <= 45]
            labels = [line['text'] for line in lines if line['size'] >= 17 and line['text'].isdigit() and line['bbox'][1]>.8*height]
            portions = re.search(r'Ingredientes\s*\((\d+)\s+raciones', page.get_text())
            original = {'physical_page':index+1,'full_page_text':page.get_text(), 'title_spans':title,
                        'ingredient_spans':ingredient_lines,'preparation_spans':preparation}
            yield base_recipe(source, info, output, path, f'page:{index+1}', source.repository+'#'+key(title_text),
                title_text, ingredients, [directions], source.repository, original_record=original,
                original_text=title_text+'\n'+'\n'.join(ingredients)+'\n'+directions,
                ingredient_segmentation='pdf-layout', extraction_method='madrid-2015-spanish-layout-v1',
                pais_origen='España', pais_iso='ESP', region='Madrid', region_evidence='document title: gastronomía madrileña',
                pagina_pdf=index+1, pagina_impresa='–'.join(labels) or None,
                raciones=int(portions[1]) if portions else None, establecimiento=' '.join(restaurant) or None,
                libro='Recetas centenarias de la gastronomía madrileña', source_url_kind='document')
