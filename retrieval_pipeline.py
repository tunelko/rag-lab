"""Focused retrieval shared by the web service and tools/evaluate_retrieval.

Three steps, each measured against labelled questions (docs/EVALUATION.md):

1. Search the native channel: text and tables, plus an image caption only when
   its page has no native text. Captions are written in the interface language
   and otherwise crowd out the original text for questions in that language.
2. When the question names a board model, search only the documents of that
   model and without the model name, which the fragments never contain.
3. Optionally reorder the first candidates with a local cross-encoder.

No model is loaded and nothing is downloaded on import.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
import re
import threading
import time
import unicodedata

VISUAL_ITEM_TYPES = frozenset({'page_image', 'figure'})
MODEL_WORDS = frozenset({
    'manual', 'motherboard', 'mainboard', 'datasheet', 'data', 'sheet', 'user',
    'guide', 'quick', 'installation', 'intel', 'amd', 'msi', 'gigabyte', 'edition',
    'english', 'german', 'french', 'russian', 'japanese', 'korean', 'chinese',
    'en', 'de', 'fr', 'ru', 'jp', 'kr', 'sc', 'tc', 'pdf',
})
CHIPSET = re.compile(r'(?:intel|amd)\W{0,3}([a-z]\d{3}[a-z]?)\W{1,3}chipset'
                     r'|chipset\W{1,3}(?:intel|amd)\W{0,3}([a-z]\d{3}[a-z]?)\b')
LOGGER = logging.getLogger('rag.retrieval')


def is_native(hit):
    return (hit.get('metadata') or {}).get('item_type') not in VISUAL_ITEM_TYPES


def normalized_tokens(value):
    normalized = unicodedata.normalize('NFKC', str(value)).casefold()
    return re.findall(r'[a-z0-9]+', normalized)


def model_aliases(value, *, title=False):
    tokens = [token for token in normalized_tokens(value)
              if token not in MODEL_WORDS and not token.isdigit()]
    aliases = set()
    minimum = 6 if title else 4
    for index, token in enumerate(tokens):
        if (len(token) < 4 or not any(char.isalpha() for char in token)
                or not any(char.isdigit() for char in token)
                or re.fullmatch(r'(?:ddr|pcie|usb|sata|lga|gen|wifi|lan)\d.*', token)):
            continue
        for start in range(max(0, index - 2), index + 1):
            for end in range(index + 1, min(len(tokens), index + 4) + 1):
                alias = ''.join(tokens[start:end])
                if len(alias) >= minimum:
                    aliases.add(alias)
    return aliases


def _snapshot_rows(store, published):
    snapshot = store.get(ids=published, include=['documents', 'metadatas'], cached=True)
    return list(zip(snapshot['ids'], snapshot['documents'], snapshot['metadatas']))


def library_catalog(store, published, rows=None):
    """Model aliases from file names and first-page titles, plus same-page neighbours."""
    rows = _snapshot_rows(store, published) if rows is None else rows
    page_one = {}
    files = set()
    neighbors = {}
    for identifier, text, metadata in rows:
        name = str(metadata.get('file') or '')
        if not name:
            continue
        files.add(name)
        if metadata.get('page') == 1 and is_native({'metadata': metadata}):
            page_one.setdefault(name, []).append((metadata.get('chunk', 0), text))
        chunk = metadata.get('chunk')
        if isinstance(chunk, int) or str(chunk).isdigit():
            key = (name.replace('\\', '/').casefold(), metadata.get('page'), int(chunk))
            neighbors[key] = {'id': identifier, 'text': text,
                              'metadata': dict(metadata), 'score': None}
    aliases = {}
    for name in files:
        candidates = model_aliases(Path(name).stem)
        title = ' '.join(text for _chunk, text in sorted(page_one.get(name, [])))[:1200]
        candidates.update(model_aliases(title, title=True))
        for alias in candidates:
            aliases.setdefault(alias, set()).add(name)
    return aliases, neighbors


def channel_catalog(store, published, rows=None):
    """Native-channel ids plus the library aliases, extended for known gaps.

    The channel keeps a caption only when its page has no native text at all.
    Extra aliases: the letters+digits prefix of a long filename token
    ("intel600bios" -> "intel600") and the chipset code stated on the first
    native pages ("Intel H110 Chipset" -> "h110") for files whose name says nothing.
    """
    rows = _snapshot_rows(store, published) if rows is None else rows
    aliases, _neighbors = library_catalog(store, published, rows)
    aliases = {alias: set(names) for alias, names in aliases.items()}
    native_pages = {(metadata.get('file'), metadata.get('page')) for _id, _text, metadata in rows
                    if is_native({'metadata': metadata})}
    channel = [identifier for identifier, _text, metadata in rows
               if is_native({'metadata': metadata})
               or (metadata.get('file'), metadata.get('page')) not in native_pages]
    for _identifier, text, metadata in rows:
        name = str(metadata.get('file') or '')
        page = metadata.get('page')
        if (not name or not is_native({'metadata': metadata})
                or not isinstance(page, int) or not 1 <= page <= 5):
            continue
        normalized = unicodedata.normalize('NFKC', str(text)).casefold()
        for match in CHIPSET.finditer(normalized):
            aliases.setdefault(match.group(1) or match.group(2), set()).add(name)
    for name in {str(metadata.get('file') or '') for _id, _text, metadata in rows} - {''}:
        for token in normalized_tokens(Path(name).stem):
            prefix = re.fullmatch(r'([a-z]+\d+)[a-z]+', token)
            if prefix and len(prefix.group(1)) >= 7:
                aliases.setdefault(prefix.group(1), set()).add(name)
    return aliases, channel


def automatic_scope_spans(question, aliases):
    """Files whose model the question names, the aliases found and their spans.

    An alias must start and end on a token boundary of the question. Without that
    rule, text in another script disappears from the compacted question and glues
    unrelated tokens together ("DDR4 のクリアCMOS" would read "ddr4cmos").
    """
    tokens = normalized_tokens(question)
    compact = ''.join(tokens)
    starts, ends, position = set(), set(), 0
    for token in tokens:
        starts.add(position)
        position += len(token)
        ends.add(position)
    matches = []
    for alias, files in aliases.items():
        start = compact.find(alias)
        while start >= 0:
            if start in starts and start + len(alias) in ends:
                matches.append((start, start + len(alias), alias, files))
            start = compact.find(alias, start + 1)
    chosen = []
    for start, end, alias, files in sorted(matches, key=lambda item: (-len(item[2]), item[0])):
        if any(start < used_end and end > used_start for used_start, used_end, _alias, _files in chosen):
            continue
        chosen.append((start, end, alias, files))
    scoped = sorted({name for _start, _end, _alias, names in chosen for name in names})
    return (scoped, sorted({alias for _start, _end, alias, _files in chosen}),
            [(start, end) for start, end, _alias, _files in chosen])


def without_identifier(question, spans):
    """Drop the words that spell the matched model alias; fragments never contain it."""
    kept = []
    position = 0
    for word in question.split():
        length = sum(len(token) for token in normalized_tokens(word))
        covered = length and any(position < end and position + length > start
                                 for start, end in spans)
        position += length
        if not covered:
            kept.append(word)
    return ' '.join(kept) or question


def load_reranker(model_id, device='auto', *, allow_download=False, half=False):
    """Local cross-encoder scorer; nothing is downloaded unless explicitly allowed."""
    import torch
    from sentence_transformers import CrossEncoder

    if device == 'auto':
        device = 'cuda' if torch.cuda.is_available() else 'cpu'
    options = {'torch_dtype': torch.float16} if half and device == 'cuda' else {}
    model = CrossEncoder(model_id, device=device, max_length=512, trust_remote_code=False,
                         local_files_only=not allow_download,
                         **({'model_kwargs': options} if options else {}))
    if device == 'cuda':
        torch.cuda.reset_peak_memory_stats()

    def score(query, texts):
        if not texts:
            return []
        return [float(value) for value in model.predict(
            [(query, text) for text in texts], batch_size=16, show_progress_bar=False)]

    def peak_vram_mib():
        return round(torch.cuda.max_memory_allocated() / 2 ** 20) if device == 'cuda' else None

    score.model_id, score.device, score.half = model_id, device, bool(options)
    score.peak_vram_mib = peak_vram_mib
    return score


def rerank(hits, query, reranker, window):
    """Reorder the first `window` hits by cross-encoder score; ties keep dense order."""
    pool = hits[:window]
    scores = reranker(query, [hit['text'] for hit in pool])
    order = sorted(range(len(pool)), key=lambda index: (-float(scores[index]), index))
    return [pool[index] for index in order] + hits[window:]


_reranker = {'key': None, 'scorer': None}
_reranker_lock = threading.Lock()


def configured_reranker():
    """The scorer named by RAG_RERANKER, loaded on first use; None when unset or unavailable.

    The model must already be in the local cache: the service never downloads it.
    A failed load is logged once and not retried until the configuration changes.
    """
    model_id = os.getenv('RAG_RERANKER', '').strip()
    if not model_id:
        return None
    return cached_reranker(model_id, os.getenv('RAG_RERANK_DEVICE', 'auto').strip() or 'auto',
                           os.getenv('RAG_RERANK_HALF', '1').strip() != '0')


def cached_reranker(model_id, device='auto', half=True):
    """Resolve an explicit, captured configuration without changing environment."""
    if not model_id:
        return None
    key = (model_id, device, half)
    with _reranker_lock:
        if _reranker['key'] != key:
            try:
                scorer = load_reranker(key[0], key[1], half=key[2])
                LOGGER.info('Reranker ready: %s on %s', scorer.model_id, scorer.device)
            except Exception:
                LOGGER.warning('Reranker %s is unavailable; answers use the dense order.',
                               model_id, exc_info=True)
                scorer = None
            _reranker.update(key=key, scorer=scorer)
        return _reranker['scorer']


def reranker_status():
    """What the service is configured to use; reading it never loads the model."""
    model_id = os.getenv('RAG_RERANKER', '').strip()
    attempted = _reranker['key'] is not None and _reranker['key'][0] == model_id
    scorer = _reranker['scorer'] if attempted else None
    return {'model': model_id or None, 'window': rerank_window() if model_id else None,
            'loaded': scorer is not None, 'device': getattr(scorer, 'device', None),
            'unavailable': bool(model_id) and attempted and scorer is None}


def rerank_window():
    try:
        return max(1, int(os.getenv('RAG_RERANK_WINDOW', '50')))
    except ValueError:
        return 50


def trace_metrics(trace):
    """Numeric and categorical retrieval facts for job metrics; never the question text."""
    names = ('strategy', 'automatic_scope', 'scope_files', 'scope_aliases', 'candidates',
             'reranked', 'reranker', 'rerank_seconds')
    return {'retrieval_' + name: trace[name] for name in names if name in trace}


_catalogs = {}
_catalog_lock = threading.Lock()


def clear_catalogs():
    """Forget cached aliases, for example after the library has been emptied."""
    with _catalog_lock:
        _catalogs.clear()


def _catalog(config, store, published):
    """Aliases and channel ids, rebuilt only when the index or its manifest changes."""
    manifest = config.db / f'{config.collection}-manifest.json'
    try:
        stat = manifest.stat()
        signature = (stat.st_mtime_ns, stat.st_size)
    except OSError:
        signature = None
    info = store.cache_info() if hasattr(store, 'cache_info') else {}
    key = (signature, info.get('loads'), info.get('generation'), len(published))
    path = str(getattr(store, 'path', manifest))
    with _catalog_lock:
        cached = _catalogs.get(path)
        if cached and cached[0] == key:
            return cached[1]
    aliases, channel = channel_catalog(store, published)
    # cache_info is read again: building the catalog may have loaded the snapshot.
    info = store.cache_info() if hasattr(store, 'cache_info') else {}
    key = (signature, info.get('loads'), info.get('generation'), len(published))
    with _catalog_lock:
        _catalogs[path] = (key, (aliases, channel))
        while len(_catalogs) > 4:
            _catalogs.pop(next(iter(_catalogs)))
    return aliases, channel


def retrieve(config, question, embeddings, k, *, files=None, visual=False,
             reranker=None, rerank_window=50, include_ids=False):
    """Return ``(hits, trace)`` for one focused question.

    ``hits`` are ``(Document, cosine_distance)`` pairs as before. ``files`` is the
    scope the user chose; automatic scope only ever narrows it. ``visual`` keeps
    every channel for questions that ask about figures or diagrams.
    ``include_ids`` adds provenance to returned metadata for evaluation only;
    the default metadata, ordering and trace of chat calls remain unchanged.
    """
    import rag
    from langchain_core.documents import Document

    trace = {'strategy': 'all-channels' if visual else 'native-channel',
             'automatic_scope': False, 'reranked': False}
    if not (config.db / f'{config.collection}-manifest.json').exists():
        raise ValueError('No index exists for this collection. Run ingest first.')
    if files is not None and not files:
        return [], trace
    published = rag.published_ids(rag.load_state(config))
    if not published:
        return [], trace
    store = rag.open_store(config, embeddings)
    aliases, channel = _catalog(config, store, published)
    query = question
    allowed = None if files is None else set(files)
    scoped, matched, spans = automatic_scope_spans(question, aliases)
    scoped = [name for name in scoped if allowed is None or name in allowed]
    if scoped:
        allowed = set(scoped)
        query = without_identifier(question, spans)
        trace.update(automatic_scope=True, scope_files=len(scoped), scope_aliases=matched,
                     search_query=query)
    options = {'ids': published if visual else channel}
    if allowed is not None:
        options['filter'] = {'file': {'$in': sorted(allowed)}}
    hits = store.dense_search_vector(store.embed_query(query), max(512, k), **options)
    hits = [hit for hit in hits if rag.usable_evidence(hit['text'])]
    trace['candidates'] = len(hits)
    if reranker is not None and hits:
        started = time.perf_counter()
        try:
            hits = rerank(hits, query, reranker, max(rerank_window, k))
            trace.update(reranked=True, reranker=getattr(reranker, 'model_id', 'custom'),
                         rerank_seconds=round(time.perf_counter() - started, 6))
        except Exception:  # A failed optional stage must not lose the answer.
            LOGGER.warning('Reranking failed; using the dense order.', exc_info=True)
    return [(Document(page_content=hit['text'], metadata=(
        {**hit['metadata'], 'vector_id': hit['id']} if include_ids else hit['metadata'])), hit['score'])
            for hit in hits[:k]], trace
