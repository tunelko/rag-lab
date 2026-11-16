"""Recipe-specific normalization. The generic RAG engine does not import this module."""
from fractions import Fraction
import hashlib
import html
import json
import re
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import BaseModel, Field


POLICY = 'recipes-v1'


def key(text):
    value = unicodedata.normalize('NFKD', str(text)).casefold()
    return ' '.join(''.join(char for char in value if not unicodedata.combining(char)).split())


def canonical_url(value):
    if not value:
        return None
    parsed = urlsplit(str(value).strip())
    if parsed.scheme not in {'http', 'https'} or not parsed.netloc:
        return None
    query = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True)
             if not k.lower().startswith('utm_') and k.lower() not in {'fbclid', 'gclid'}]
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path.rstrip('/') or '/', urlencode(sorted(query)), ''))


def stable_id(dataset, identifier, url, title):
    identity = str(identifier) if identifier not in (None, '') else canonical_url(url) or key(title)
    return 'recipe_' + hashlib.sha256((dataset + ':' + identity).encode()).hexdigest()[:24]


def json_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def clean_text(value):
    text = '' if value is None else str(value)
    # Obsidian links stay as readable labels; no external/internal resource is fetched.
    text = re.sub(r'\[\[([^\]|]+)\|([^\]]+)\]\]', r'\2', text)
    text = re.sub(r'\[\[([^\]]+)\]\]', r'\1', text)
    text = re.sub(r'<br\s*/?>|</(?:p|li|div)>', '\n', text, flags=re.I)
    text = re.sub(r'<[^>]+>', '', text)
    return html.unescape(text).strip()


FRACTIONS = {'¼': '1/4', '½': '1/2', '¾': '3/4', '⅓': '1/3', '⅔': '2/3', '⅛': '1/8', '⅜': '3/8', '⅝': '5/8', '⅞': '7/8'}
NUMBERS = {'un': 1, 'una': 1, 'uno': 1, 'dos': 2, 'tres': 3, 'cuatro': 4, 'cinco': 5, 'seis': 6,
           'siete': 7, 'ocho': 8, 'nueve': 9, 'diez': 10, 'once': 11, 'doce': 12}
UNITS = {'g': 'g', 'gr': 'g', 'grs': 'g', 'gramo': 'g', 'gramos': 'g', 'kg': 'kg', 'kilo': 'kg', 'kilos': 'kg',
         'kilogramo': 'kg', 'kilogramos': 'kg', 'ml': 'ml', 'mililitros': 'ml', 'l': 'l', 'litro': 'l', 'litros': 'l',
         'cucharada': 'cucharada', 'cucharadas': 'cucharada', 'cucharadita': 'cucharadita', 'cucharaditas': 'cucharadita',
         'taza': 'taza', 'tazas': 'taza', 'vaso': 'vaso', 'vasos': 'vaso', 'diente': 'diente', 'dientes': 'diente',
         'pieza': 'unidad', 'piezas': 'unidad', 'unidad': 'unidad', 'unidades': 'unidad'}
PLURALS = {'patatas': 'patata', 'huevos': 'huevo', 'cebollas': 'cebolla', 'zanahorias': 'zanahoria',
           'tomates': 'tomate', 'pimientos': 'pimiento', 'garbanzos': 'garbanzo', 'lentejas': 'lenteja'}


class Ingredient(BaseModel):
    original: str
    ingrediente: str
    cantidad: float | None = None
    unidad: str | None = None
    descripcion: str | None = None


def ingredient(value):
    original = str(value)
    text = clean_text(value).strip(' -•\t')
    parse = text
    for symbol, expanded in FRACTIONS.items():
        parse = parse.replace(symbol, ' ' + expanded + ' ')
    parse = ' '.join(parse.split())
    amount = None
    match = re.match(r'^(\d+\s+\d+/\d+|\d+/\d+|\d+(?:[.,]\d+)?)(?=\s|[a-zA-Z])\s*', parse)
    if match:
        try:
            parts = match[1].replace(',', '.').split()
            amount = float(sum(Fraction(part) for part in parts))
            parse = parse[match.end():]
        except (ValueError, ZeroDivisionError):
            pass
    else:
        word = re.match(r'^(un[ao]?|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez|once|doce)\s+', key(parse))
        if word:
            amount = float(NUMBERS[word[1]]);parse = parse[word.end():]
    unit = None
    token = re.match(r'^([\w]+)\.?\s+(?:de\s+)?', parse, flags=re.U)
    if token and key(token[1]) in UNITS:
        unit = UNITS[key(token[1])];parse = parse[token.end():]
    elif amount is not None:
        unit = 'unidad'
    parse = re.sub(r'^de\s+', '', parse, flags=re.I)
    description = None
    modifier = re.search(r'\s+(al gusto|grande|grandes|pequeña|pequeñas|mediana|medianas)$', parse, flags=re.I)
    if modifier:
        description = modifier[1];parse = parse[:modifier.start()]
    name = key(parse).strip(' ,.;')
    name = ' '.join(PLURALS.get(part, part) for part in name.split())
    return Ingredient(original=original, ingrediente=name, cantidad=amount, unidad=unit, descripcion=description)


class Recipe(BaseModel):
    recipe_id: str
    titulo: str
    ingredientes: list[str] = Field(default_factory=list)
    elaboracion: list[str] = Field(default_factory=list)
    source_dataset: str
    source_url: str
    source_url_kind: str = 'record'
    source_attribution: dict = Field(default_factory=dict)
    source_file: str
    license_status: str
    source_record: str
    original_record: dict = Field(default_factory=dict)
    original_text: str = ''
    original_hash: str = ''
    content_hash: str = ''
    ingredientes_normalizados: list[Ingredient] = Field(default_factory=list)
    ingredient_count: int | None = None
    ingredient_segmentation: str | None = None
    raciones: int | None = None
    tiempo_preparacion_min: int | None = None
    tiempo_coccion_min: int | None = None
    tiempo_total_min: int | None = None
    dificultad: str | None = None
    categoria: str | None = None
    categoria_original: str | list | None = None
    subcategoria: str | None = None
    pais_origen: str | None = None
    pais_original: str | None = None
    pais_iso: str | None = None
    region: str | None = None
    region_evidence: str | None = None
    tipo_cocina: list[str] = Field(default_factory=list)
    tipo_dieta: list[str] = Field(default_factory=list)
    alergenos: list[str] = Field(default_factory=list)
    temporada: list[str] = Field(default_factory=list)
    autor: str | None = None
    libro: str | None = None
    anio_publicacion: int | None = None
    pagina_pdf: int | None = None
    pagina_impresa: str | None = None
    establecimiento: str | None = None
    valor_nutricional: dict | str | None = None
    idioma: str = 'es'
    es_historica: bool = False
    extraction_method: str
    quality_score: int = 0
    needs_review: bool = False
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    additional_text: str = ''


COUNTRIES = dict(zip(('ESP ARG BOL CHL COL CRI CUB DOM ECU GTM HND MEX NIC PAN PER PRI PRY SLV URY VEN').split(),
    ('España|Argentina|Bolivia|Chile|Colombia|Costa Rica|Cuba|República Dominicana|Ecuador|Guatemala|Honduras|México|Nicaragua|Panamá|Perú|Puerto Rico|Paraguay|El Salvador|Uruguay|Venezuela').split('|')))
REGIONS = {'asturian': 'Asturias', 'galleg': 'Galicia', 'cantabr': 'Cantabria', 'vasc': 'País Vasco',
           'madrilen': 'Madrid', 'andaluz': 'Andalucía', 'andaluza': 'Andalucía', 'valencian': 'Comunidad Valenciana',
           'catalan': 'Cataluña', 'aragones': 'Aragón', 'riojan': 'La Rioja', 'navarr': 'Navarra',
           'extremen': 'Extremadura', 'murcian': 'Región de Murcia', 'canari': 'Canarias', 'mancheg': 'Castilla-La Mancha'}


def normalize(recipe):
    if recipe.ingredient_segmentation in {'unstructured-text','unsplit'}:
        recipe.ingredientes_normalizados=[Ingredient(original=item,ingrediente=key(clean_text(item)),descripcion='segmentacion_no_verificada') for item in recipe.ingredientes]
    else:
        recipe.ingredientes_normalizados = [ingredient(item) for item in recipe.ingredientes]
    if recipe.ingredient_segmentation in {'list', 'lines', 'space-blocks', 'pdf-layout', 'markdown-list'}:
        recipe.ingredient_count = len(recipe.ingredientes)
    if recipe.pais_original:
        country = recipe.pais_original.strip()
        iso = country.upper()
        if iso in COUNTRIES:
            recipe.pais_iso = iso;recipe.pais_origen = COUNTRIES[iso]
        elif key(country) in {'espana', 'spain', 'es'}:
            recipe.pais_iso = 'ESP';recipe.pais_origen = 'España'
    if recipe.pais_origen == 'España' and not recipe.region:
        for word in re.findall(r'\w+', key(recipe.titulo)):
            for prefix, region in REGIONS.items():
                if word.startswith(prefix):
                    recipe.region = region;recipe.region_evidence = 'titulo: ' + word;break
    recipe.content_hash = json_hash([key(recipe.titulo), [key(item) for item in recipe.ingredientes],
                                    [key(item) for item in recipe.elaboracion]])
    recipe.original_hash = json_hash(recipe.original_record)
    return recipe


def validate(recipe):
    errors = list(recipe.errors)
    if not recipe.titulo.strip():errors.append('empty_title')
    if re.match(r'^\d+\s+(?:recetas|ideas|platos)\b',key(recipe.titulo)):errors.append('aggregate_not_single_recipe')
    if not recipe.ingredientes or not any(re.search(r'[a-zA-ZáéíóúñÁÉÍÓÚÑ]', item) for item in recipe.ingredientes):errors.append('missing_ingredients')
    text = '\n'.join(recipe.elaboracion)
    if len(text.strip()) < 30 or len(text.split()) < 6:errors.append('insufficient_preparation')
    if not recipe.source_url or not recipe.source_file:errors.append('missing_provenance')
    if recipe.idioma != 'es':errors.append('non_spanish_source')
    if recipe.ingredient_segmentation == 'unsplit':errors.append('unresolved_ingredient_boundaries')
    combined = recipe.titulo + text + ' '.join(recipe.ingredientes)
    if '\ufffd' in combined or re.search(r'Ã[\u0080-\u00bf]|Â[\u0080-\u00bf]', combined):errors.append('possible_decode_damage')
    if re.search(r'<(?:script|iframe|div|html)\b', combined, re.I):errors.append('residual_html')
    recipe.errors = sorted(set(errors));recipe.needs_review = bool(recipe.errors)
    penalties = {'empty_title': 35, 'missing_ingredients': 35, 'insufficient_preparation': 35,
                 'non_spanish_source': 40, 'possible_decode_damage': 25, 'unresolved_ingredient_boundaries': 25}
    recipe.quality_score = max(0, 100 - sum(penalties.get(error, 20) for error in recipe.errors))
    if recipe.es_historica and any(re.search(r'\b(?:quartillo|cuartillo|libra|onza|ounce)\b', item, re.I) for item in recipe.ingredientes):
        recipe.warnings.append('historical_units_preserved_no_conversion')
    return recipe
