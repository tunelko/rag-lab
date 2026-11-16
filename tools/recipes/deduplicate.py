"""Conservative, auditable duplicates. A shared cookbook URL is not a recipe ID."""
from difflib import SequenceMatcher
import re

from .models import canonical_url, key


def provenance(recipe):
    return {name:recipe.get(name) for name in ('recipe_id','source_dataset','source_record','source_file',
        'source_url','source_url_kind','source_attribution','license_status','original_hash')}


def quantity_signature(recipe):
    return [re.findall(r'\d+(?:[.,/]\d+)?|[¼½¾⅓⅔⅛⅜⅝⅞]', item) for item in recipe['ingredientes']]


def compare(left, right, title_threshold=98, ingredient_threshold=.95, steps_threshold=98):
    title = SequenceMatcher(None,key(left['titulo']),key(right['titulo']),autojunk=False).ratio()*100
    if title < title_threshold or left.get('raciones') != right.get('raciones') or quantity_signature(left) != quantity_signature(right):return None
    first = {key(item) for item in left['ingredientes']};second = {key(item) for item in right['ingredientes']}
    ingredients = len(first & second)/len(first | second) if first | second else 0
    if ingredients < ingredient_threshold:return None
    preparation = SequenceMatcher(None,key(' '.join(left['elaboracion'])),key(' '.join(right['elaboracion'])),autojunk=False).ratio()*100
    if preparation < steps_threshold:return None
    return {'title_similarity':round(title,3),'ingredient_jaccard':round(ingredients,5),'preparation_similarity':round(preparation,3)}


def deduplicate(records, source_order, *, title_threshold=98, ingredient_threshold=.95, steps_threshold=98, max_candidates=200, observer=None):
    masters, review, events = {}, [], []
    hashes, urls, identities, blocks = {}, {}, {}, {}
    comparisons, crowded = 0, 0
    ordered = sorted(records,key=lambda item:(source_order.get(item['source_dataset'],999),item['recipe_id'],item['original_hash']))
    for processed,recipe in enumerate(ordered,1):
        if observer and processed%2500==0:observer(processed,len(ordered),len(masters))
        if recipe['needs_review']:
            review.append(recipe);continue
        identifier = recipe['recipe_id'];url = canonical_url(recipe['source_url']) if recipe.get('source_url_kind')=='record' else None
        duplicate = identities.get(identifier) or hashes.get(recipe['content_hash']) or (urls.get(url) if url else None)
        reason = 'source_id' if identifier in identities else 'content_sha256' if recipe['content_hash'] in hashes else 'canonical_record_url' if duplicate else None
        facts = {}
        title = key(recipe['titulo']);words = title.split()
        block_keys = {title[:12], ' '.join(words[-2:])[:18]}
        if not duplicate:
            candidates = sorted({candidate for block in block_keys for candidate in blocks.get(block,[])})
            if len(candidates)>max_candidates:crowded += len(candidates)-max_candidates
            for candidate in candidates[:max_candidates]:
                comparisons += 1
                facts = compare(recipe,masters[candidate],title_threshold,ingredient_threshold,steps_threshold)
                if facts:
                    duplicate=candidate;reason='approximate_high_confidence';break
        if duplicate:
            master=masters[duplicate];master['provenance'].append(provenance(recipe))
            # Only fill absent typed metadata using an explicitly identified source.
            for name in ('pais_origen','pais_iso','pais_original','region','region_evidence','raciones','tiempo_total_min','valor_nutricional'):
                if master.get(name) is None and recipe.get(name) is not None:
                    master[name]=recipe[name]
                    master.setdefault('metadata_provenance',{})[name]=provenance(recipe)
                elif master.get(name) is not None and recipe.get(name) is not None and master[name]!=recipe[name]:
                    conflict={'field':name,'primary':master[name],'other':recipe[name],'source':provenance(recipe)}
                    master.setdefault('metadata_conflicts',[]).append(conflict)
            events.append({'recipe_id':identifier,'canonical_id':duplicate,'reason':reason,**(facts or {})})
            identities[identifier]=duplicate;hashes[recipe['content_hash']]=duplicate
            if url:urls[url]=duplicate
            continue
        recipe={**recipe,'provenance':[provenance(recipe)]};masters[identifier]=recipe
        identities[identifier]=identifier;hashes[recipe['content_hash']]=identifier
        if url:urls[url]=identifier
        for block in block_keys:blocks.setdefault(block,[]).append(identifier)
    return list(masters.values()),review,events,{'approximate_comparisons':comparisons,'bounded_candidates_not_compared':crowded,
        'title_threshold':title_threshold,'ingredient_threshold':ingredient_threshold,'steps_threshold':steps_threshold,'max_candidates':max_candidates}
