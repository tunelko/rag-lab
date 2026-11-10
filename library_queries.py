"""Local query scope, exact library inventory and balanced cross-document retrieval."""
from dataclasses import dataclass
from collections import Counter
import math
from pathlib import Path
import re
from urllib.parse import quote

import numpy as np

import rag
from model_routing import normalize


CATALOG_WORDS = set(('y el la los las de del en un una por favor me puedes dime indica indicame '
    'que cuales cuantos cuantas numero cantidad total lista listado listar enumera enumerar '
    'nombres nombre documentos documento archivos archivo manual manuales biblioteca '
    'tienes tiene tenemos tengo hay estan esta son se indexado indexada indexados indexadas '
    'hasta ahora actualmente disponibles disponible cargados cargadas cuantos '
    'and the a an of in please tell me what which how many count number total list '
    'names name documents document files file manuals manual library indexed loaded '
    'do you have are is there currently available').split())
GLOBAL = re.compile(r'\b(?:tod[oa]s?|all)\b.{0,60}\b(?:documentos?|archivos?|manuales?|biblioteca|documents?|files?|manuals?|library)\b'
                    r'|\b(?:cada|each)\b.{0,45}\b(?:documento|archivo|manual|placa|equipo|document|file|motherboard|device)\b')
ALIASES = {word: canonical for canonical, words in {
    'cpu': 'cpu processor processors procesador procesadores',
    'support': 'support supports supported compatible compatibles compatibility compatibilidad soporta soportan soportadas soportados soportado soportada soporte admite admiten admitidos admitidas',
    'memory': 'memory memoria memorias ram', 'storage': 'storage almacenamiento',
    'port': 'port ports puerto puertos', 'connector': 'connector connectors conector conectores',
}.items() for word in words.split()}
STOP_WORDS = set('el la los las de del a y o con para por en un una the of and or for in with a an'.split())


def tokens(text):
    text = normalize(re.sub(r'支持|支援|サポート|対応', ' support ', text))
    return [ALIASES.get(word, word) for word in re.findall(r'\w+', text) if word not in STOP_WORDS]


def best_evidence(texts, semantic_scores, topic, metadata=None):
    """Prefer literal field phrases; otherwise fuse cosine and BM25 rankings."""
    terms = list(dict.fromkeys(tokens(topic)))
    if not terms:
        return int(np.argmax(semantic_scores))
    counts = [Counter(tokens(text)) for text in texts]
    lengths = np.asarray([sum(count.values()) for count in counts], dtype=np.float64)
    average = max(float(lengths.mean()), 1)
    lexical = np.zeros(len(texts), dtype=np.float64)
    for term in terms:
        frequencies = np.asarray([count[term] for count in counts], dtype=np.float64)
        found = int(np.count_nonzero(frequencies))
        idf = math.log(1 + (len(texts) - found + .5) / (found + .5))
        lexical += idf * frequencies * 2.2 / (frequencies + 1.2 * (.25 + .75 * lengths / average))
    semantic_order = np.argsort(-semantic_scores, kind='stable')
    lexical_order = np.argsort(-lexical, kind='stable')
    fused = np.zeros(len(texts), dtype=np.float64)
    for rank, index in enumerate(semantic_order, 1):
        fused[index] += 1 / (60 + rank)
    if lexical.max() > 0:
        for rank, index in enumerate(lexical_order, 1):
            fused[index] += 1 / (60 + rank)
    # Exact, canonicalized multiword fields such as "CPU support" are explicit evidence.
    # Long questions use fused rankings rather than requiring an entire sentence verbatim.
    phrase = ' '.join(terms)
    exact = [index for index, text in enumerate(texts)
             if 1 < len(terms) <= 4 and (' ' + phrase + ' ') in (' ' + ' '.join(tokens(text)) + ' ')]
    field = re.compile(r'^\s*[|#*`\s]*' + re.escape(terms[0]) + r'(?:\s*[*`]*\s*\||[*`\s]*$)', re.MULTILINE | re.IGNORECASE)
    fields = [index for index, text in enumerate(texts) if 1 < len(terms) <= 4 and field.search(normalize(text))
              and all(counts[index][term] for term in terms)]
    native_fields = [index for index in fields if metadata and metadata[index].get('item_type') in ('text', 'table')]
    native = [index for index in exact if metadata and metadata[index].get('item_type') in ('text', 'table')]
    candidates = native_fields or fields or native or exact or range(len(texts))
    return max(candidates, key=lambda index: fused[index])


@dataclass(frozen=True)
class Intent:
    mode: str = 'focused'
    topic: str = ''


def query_intent(question):
    text = normalize(question)
    words = set(re.findall(r'\w+', text))
    catalog = bool(words) and not (words - CATALOG_WORDS)
    document_words = {'documentos', 'documento', 'archivos', 'archivo', 'manuales', 'manual', 'biblioteca',
                      'indexado', 'indexados', 'indexadas', 'documents', 'files', 'manuals', 'library', 'indexed'}
    if catalog and words & document_words:
        if words & {'cuantos', 'cuantas', 'cantidad', 'numero', 'many', 'count', 'number'}:
            return Intent('catalog_count')
        if words & {'que', 'cuales', 'lista', 'listado', 'listar', 'enumera', 'enumerar', 'nombres', 'which', 'what', 'list', 'names'}:
            return Intent('catalog_list')
    if GLOBAL.search(text):
        return Intent('global', topic_query(question))
    return Intent('focused', question.strip())


def topic_query(question):
    text = normalize(question)
    # Strip corpus-selection language and reporting instructions, not subject terms.
    text = re.sub(r'^(?:de |en |entre |from |in |across )?(?:tod[oa]s?|all)(?: los| las| the)? '
                  r'(?:documentos|archivos|manuales|documents|files|manuals)(?: indexad[oa]s| indexed)?\s*[,;:]?\s*', '', text)
    text = re.sub(r'\b(?:por|de|para|for|of) (?:cada|each)\b.*$', '', text)
    text = re.sub(r'^(?:enumera|enumerar|lista|listar|compara|comparar|dime|list|compare|enumerate)\s+', '', text)
    text = re.sub(r'^(?:el|la|los|las|the)\s+', '', text)
    return text.strip(' ,;:.?¿¡!') or question.strip()


def catalog_answer(documents, mode):
    indexed = [item for item in documents if item.get('indexed')]
    chunks = sum(item.get('chunks', 0) for item in indexed)
    formatted_chunks = f'{chunks:,}'.replace(',', '.')
    answer = f'Hay **{len(indexed)} documentos indexados**, con **{formatted_chunks} fragmentos**.'
    if mode == 'catalog_list':
        def label(name):
            return re.sub(r'([\\`*{}\[\]()#+.!_<>])', r'\\\1', name)
        answer += '\n\n' + '\n'.join(f'- [{label(item["name"])}](/api/source?file={quote(item["name"], safe="")})' for item in indexed)
    pending = len(documents) - len(indexed)
    if pending:
        answer += f'\n\nOtros **{pending} documentos** todavía no tienen un índice actualizado.'
    return answer, {'backend': 'catalog', 'model': 'Registro del índice', 'retrieval_mode': 'catalog',
                    'routing_mode': 'catalog', 'routing_reason': 'Recuento exacto del registro; sin generación.',
                    'documents_indexed': len(indexed), 'fragments_indexed': chunks}


def global_retrieve(config, documents, embeddings, question, *, cancelled=lambda: False, observer=None):
    """Scan indexed vectors exactly per document; allocate context to every file."""
    indexed = sorted((item for item in documents if item.get('indexed')), key=lambda item: item['name'])
    if not indexed:
        return [], {'retrieval_mode': 'global', 'documents_indexed': 0, 'documents_retrieved': 0}
    allowance = config.context_chars // len(indexed)
    if allowance < 160:
        raise ValueError('El contexto configurado no permite representar todos los documentos. '
                         'Amplía RAG_CONTEXT_CHARS o acota la biblioteca; no se enviará una muestra como si fuera completa.')
    topic = topic_query(question)
    query = np.asarray(embeddings.embed_query(topic), dtype=np.float32)
    norm = np.linalg.norm(query)
    if not np.isfinite(norm) or norm == 0:
        raise ValueError('El embedding de la consulta no es válido.')
    query = query / norm
    store = rag.open_store(config, embeddings)
    hits = []
    for number, item in enumerate(indexed, 1):
        if cancelled():
            raise InterruptedError('Búsqueda global cancelada.')
        rows = store.get(where={'file': item['name']}, include=['documents', 'metadatas', 'embeddings'])
        if not rows['ids']:
            raise ValueError(f'No se han recuperado los fragmentos de {item["name"]}; revisa su índice. '
                             'La consulta global se ha detenido para no omitir ese documento.')
        matrix = np.asarray(rows['embeddings'], dtype=np.float32)
        norms = np.linalg.norm(matrix, axis=1)
        scores = matrix @ query / np.maximum(norms, np.finfo(np.float32).tiny)
        if not np.all(np.isfinite(scores)):
            raise ValueError('El índice contiene embeddings no válidos.')
        best = best_evidence(rows['documents'], scores, topic, rows['metadatas'])
        document = rag.Document(page_content=rows['documents'][best][:allowance], metadata=rows['metadatas'][best])
        hits.append((document, float(1 - np.clip(scores[best], -1, 1))))
        if observer:
            observer(number, len(indexed))
    return hits, {'retrieval_mode': 'global', 'documents_indexed': len(indexed),
                  'documents_retrieved': len(hits), 'coverage_complete': True, 'search_topic': topic}
