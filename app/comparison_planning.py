"""A comparison that needs clarification has no evidence to cite."""
from dataclasses import dataclass
import re
from typing import Literal

from model_routing import normalize


def quoted_condition(question, value, *, previous=''):
    """Return only a literal restriction from the request; an entire question is not a filter."""
    if not isinstance(value, str) or not re.search(r'\w', value):
        return None
    pattern = r'(?<!\w)' + re.escape(value.strip()) + r'(?!\w)'
    for text in (question, previous):
        match = re.search(pattern, text, re.IGNORECASE)
        if match and re.findall(r'\w+', normalize(match[0])) != re.findall(r'\w+', normalize(text)):
            return match[0]
    return None

@dataclass(frozen=True)
class ComparisonClarification:
    reason: Literal['selection_by_condition', 'missing_operands', 'invalid_plan', 'unsupported_restriction', 'same_operand', 'unresolved_operand']
    terms: tuple[str, ...] = ()

    def answer(self, query, metrics, scope_label=None, *, documents=False):
        es_names = 'los nombres de los documentos' if documents else 'los nombres o identificadores de las entidades'
        en_names = 'the document names' if documents else 'the entity names or identifiers'
        quoted = ' «' + ', '.join(re.sub(r'([^\w\s])', r'\\\1', term) for term in self.terms) + '»' if self.terms else ''
        if self.reason == 'selection_by_condition':
            es = f'Primero tendría que verificar qué elementos cumplen la condición{quoted}. Para hacer el cruce actual, indica {es_names} que quieres comparar.'
            en = f'I would first need to verify which items meet the condition{quoted}. For the current comparison, please give {en_names}.'
        elif self.reason == 'unsupported_restriction':
            es = f'El cruce disponible no permite verificar el filtro que pides{quoted}.'
            en = f'The available comparison cannot verify the requested filter{quoted}.'
        elif self.reason == 'same_operand':
            es = 'Varios nombres corresponden a la misma entidad. Indica las entidades distintas que quieres comparar.'
            en = 'Multiple names refer to the same entity. Please specify the distinct entities to compare.'
        elif self.reason == 'unresolved_operand':
            es = 'No he podido identificar de forma inequívoca todos los elementos en las fuentes seleccionadas. Indica sus identificadores o nombres completos.'
            en = 'I could not uniquely identify every item in the selected sources. Please give their identifiers or full names.'
        elif self.reason == 'missing_operands':
            es = f'¿Qué quieres comparar? Indica {es_names} de al menos dos elementos.'
            en = f'Which items would you like to compare? Please give {en_names} for at least two items.'
        else:
            es = 'Musa no ha podido interpretar la comparación con suficiente fiabilidad para consultar las fuentes.'
            en = 'Musa could not interpret the comparison reliably enough to query the sources.'
        answer = en if query.language == 'en' else es
        return {'answer': answer, 'sources': [],
                'metrics': {**metrics, 'retrieval_mode': 'intersection', 'needs_clarification': True,
                            'planning_status': self.reason, 'clarification_terms': list(self.terms),
                            'topic_id': query.topic_id, 'scope_label': scope_label}}
