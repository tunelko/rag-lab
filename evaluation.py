"""Deterministic retrieval and answer metrics; no models, network or index writes."""
from __future__ import annotations

import math
import re
import statistics
import unicodedata


def _file(value):
    return str(value or '').replace('\\', '/').casefold()


def qrel_key(qrel):
    if qrel.get('vector_id'):
        return 'vector:' + str(qrel['vector_id'])
    if not qrel.get('file'):
        raise ValueError('Each relevance judgment needs vector_id or file.')
    key = 'file:' + _file(qrel['file'])
    if qrel.get('page') is not None:
        key += '#page:' + str(int(qrel['page']))
    return key


def hit_matches(hit, qrel):
    if qrel.get('vector_id'):
        return hit.get('id') == qrel['vector_id']
    metadata = hit.get('metadata') or {}
    if _file(metadata.get('file')) != _file(qrel.get('file')):
        return False
    return qrel.get('page') is None or int(metadata.get('page', -1)) == int(qrel['page'])


def _evidence_text(value):
    return re.sub(r'\s+', ' ', unicodedata.normalize('NFKC', str(value)).casefold()).strip()


def score_evidence(hits, qrels, evidence, *, ks=(1, 5, 10)):
    """Detect literal answer anchors inside relevant evidence actually sent at K."""
    if not isinstance(evidence, list) or not evidence or not all(
            isinstance(value, str) and value.strip() for value in evidence):
        raise ValueError('Evidence must be a nonempty list of nonempty literal strings.')
    anchors = [_evidence_text(value) for value in evidence]
    result = {}
    for k in sorted(set(int(value) for value in ks)):
        if k < 1:
            raise ValueError('Metric cutoffs must be positive.')
        buckets = {}
        for position, hit in enumerate(hits[:k]):
            metadata = hit.get('metadata') or {}
            for qrel in qrels:
                if not hit_matches(hit, qrel):
                    continue
                # Never manufacture an answer by joining different physical pages.
                bucket = (qrel_key(qrel), _file(metadata.get('file')), metadata.get('page'))
                chunk = metadata.get('chunk')
                order = int(chunk) if isinstance(chunk, int) or str(chunk).isdigit() else position
                buckets.setdefault(bucket, []).append((order, position, hit.get('text', '')))
        covered = False
        for fragments in buckets.values():
            joined = _evidence_text(' '.join(
                text for _order, _position, text in sorted(fragments)))
            if any(anchor in joined for anchor in anchors):
                covered = True
                break
        result[f'evidence_recall@{k}'] = 1.0 if covered else 0.0
    return result


def score_ranking(hits, qrels, *, ks=(1, 5, 10)):
    """Score one ranked list against binary or graded file/page/chunk judgments."""
    if not qrels:
        raise ValueError('At least one relevance judgment is required.')
    judgments = {}
    target_groups = {}
    normalized_qrels = []
    granularities = set()
    for qrel in qrels:
        relevance = float(qrel.get('relevance', 1))
        if not math.isfinite(relevance) or relevance <= 0:
            raise ValueError('Relevance must be a positive finite number.')
        target = qrel_key(qrel)
        group = qrel.get('group')
        if group is not None and (not isinstance(group, str) or not group.strip()):
            raise ValueError('Judgment group must be a nonempty string.')
        group_key = 'group:' + group.strip() if group is not None else 'judgment:' + target
        previous_group = target_groups.get(target)
        if previous_group is not None and previous_group != group_key:
            raise ValueError('The same relevance target cannot belong to different groups.')
        previous_relevance = judgments.get(group_key)
        if previous_relevance is not None and previous_relevance != relevance:
            raise ValueError('Equivalent judgments in one group need the same relevance.')
        target_groups[target] = group_key
        judgments[group_key] = relevance
        normalized_qrels.append((qrel, group_key))
        granularities.add('vector' if qrel.get('vector_id') else
                          'page' if qrel.get('page') is not None else 'file')
    if len(granularities) != 1:
        raise ValueError('Use one relevance granularity per query: vector, page or file.')
    granularity = next(iter(granularities))
    ranked = []
    ranked_keys = set()
    seen = set()
    for hit in hits:
        metadata = hit.get('metadata') or {}
        if granularity == 'vector':
            hit_key = 'vector:' + str(hit.get('id'))
        elif granularity == 'page':
            hit_key = 'file:' + _file(metadata.get('file')) + '#page:' + str(metadata.get('page'))
        else:
            hit_key = 'file:' + _file(metadata.get('file'))
        if hit_key in ranked_keys:
            continue
        ranked_keys.add(hit_key)
        matches = [(group_key, judgments[group_key])
                   for qrel, group_key in normalized_qrels if hit_matches(hit, qrel)]
        unseen = [(key, relevance) for key, relevance in matches if key not in seen]
        if unseen:
            key, relevance = max(unseen, key=lambda item: item[1])
            seen.add(key)
            ranked.append(relevance)
        elif matches:
            # Another file/page/chunk that is explicitly equivalent to evidence
            # already covered does not gain or consume a collapsed rank.
            continue
        else:
            ranked.append(0.0)
    result = {}
    total = len(judgments)
    ideal_values = sorted(judgments.values(), reverse=True)
    for k in sorted(set(int(value) for value in ks)):
        if k < 1:
            raise ValueError('Metric cutoffs must be positive.')
        found = sum(value > 0 for value in ranked[:k])
        raw_found = {group_key for hit in hits[:k]
                     for qrel, group_key in normalized_qrels if hit_matches(hit, qrel)}
        result[f'recall@{k}'] = found / total
        result[f'fragment_recall@{k}'] = len(raw_found) / total
        result[f'precision@{k}'] = found / k
        first = next((index for index, value in enumerate(ranked[:k], 1) if value > 0), None)
        result[f'mrr@{k}'] = 1 / first if first else 0.0
        actual = sum((2 ** value - 1) / math.log2(index + 2)
                     for index, value in enumerate(ranked[:k]))
        ideal = sum((2 ** value - 1) / math.log2(index + 2)
                    for index, value in enumerate(ideal_values[:k]))
        result[f'ndcg@{k}'] = actual / ideal if ideal else 0.0
    result['relevant_total'] = total
    result['relevant_retrieved'] = len(seen)
    return result


def aggregate_rankings(per_query):
    if not per_query:
        raise ValueError('No query results to aggregate.')
    metric_names = sorted({name for item in per_query for name in item['metrics']
                           if name not in {'relevant_total', 'relevant_retrieved'}})
    result = {}
    for name in metric_names:
        values = [item['metrics'][name] for item in per_query if name in item['metrics']]
        result[name] = statistics.fmean(values)
        if name.startswith('evidence_recall@'):
            result['evidence_queries'] = len(values)
    latencies = sorted(float(item['latency_ms']) for item in per_query)
    result.update({
        'queries': len(per_query),
        'latency_ms_p50': _percentile(latencies, 50),
        'latency_ms_p95': _percentile(latencies, 95),
        'latency_ms_mean': statistics.fmean(latencies),
    })
    for field in ('embedding_ms', 'search_ms'):
        if all(field in item for item in per_query):
            values = sorted(float(item[field]) for item in per_query)
            result[f'{field}_p50'] = _percentile(values, 50)
            result[f'{field}_p95'] = _percentile(values, 95)
    return result


def _percentile(values, percentile):
    if not values:
        raise ValueError('No values for percentile.')
    if len(values) == 1:
        return values[0]
    position = (len(values) - 1) * percentile / 100
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return values[low]
    return values[low] * (high - position) + values[high] * (position - low)


def _answer_text(value):
    return ' '.join(str(value or '').casefold().split())


def _edit_distance(left, right):
    if len(left) < len(right):
        left, right = right, left
    previous = list(range(len(right) + 1))
    for row, character in enumerate(left, 1):
        current = [row]
        for column, other in enumerate(right, 1):
            current.append(min(current[-1] + 1, previous[column] + 1,
                               previous[column - 1] + (character != other)))
        previous = current
    return previous[-1]


def anls(prediction, references, *, threshold=0.5):
    """Average Normalized Levenshtein Similarity used for short DocVQA answers."""
    predicted = _answer_text(prediction)
    if isinstance(references, str):
        references = [references]
    scores = []
    for reference in references:
        expected = _answer_text(reference)
        length = max(len(predicted), len(expected))
        similarity = 1 - _edit_distance(predicted, expected) / length if length else 1.0
        scores.append(similarity if similarity >= threshold else 0.0)
    if not scores:
        raise ValueError('At least one reference answer is required.')
    return max(scores)


def score_citations(citations, qrels):
    """Page/file citation precision and recall, independent from answer wording."""
    if not qrels:
        raise ValueError('At least one relevance judgment is required.')
    expected = {qrel_key(qrel) for qrel in qrels}
    matched = set()
    correct = 0
    for citation in citations:
        possible = [(qrel_key(qrel), qrel) for qrel in qrels if hit_matches(
            {'id': citation.get('vector_id'), 'metadata': citation}, qrel)]
        if possible:
            correct += 1
            matched.add(possible[0][0])
    return {
        'citation_precision': correct / len(citations) if citations else 0.0,
        'citation_recall': len(matched) / len(expected),
    }


def score_abstention(items):
    """Measure whether unanswered questions are refused and answerable ones answered."""
    if not items:
        raise ValueError('No abstention judgments.')
    tp = sum(not item['answerable'] and item['abstained'] for item in items)
    fp = sum(item['answerable'] and item['abstained'] for item in items)
    fn = sum(not item['answerable'] and not item['abstained'] for item in items)
    return {
        'abstention_precision': tp / (tp + fp) if tp + fp else 0.0,
        'abstention_recall': tp / (tp + fn) if tp + fn else 0.0,
        'answer_decision_accuracy': sum(item['answerable'] != item['abstained'] for item in items) / len(items),
    }
