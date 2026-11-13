"""Shared deterministic lexical settings for production ranking and evaluation."""

FTS_TOKENIZER = 'unicode61 remove_diacritics 2'

# Production global BM25 behavior is compatibility-sensitive. Changing this set
# can change which fragment represents a document and requires a labeled benchmark.
GLOBAL_RANKING_STOP_WORDS = frozenset(
    'el la los las de del a y o con para por en un una the of and or for in with a an'.split())

# Experimental FTS has its own broader multilingual list; it must not silently
# alter the established per-document production ranker above.
FTS_STOP_WORDS = frozenset('''
a al an and are as at be by con das de del der des die do el en es et for from für
in is la las le les los mit o of on or para por que the to un una und von with y
'''.split())
