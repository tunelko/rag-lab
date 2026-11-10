"""Validate one answer per retrieved file; the app owns document names and citations."""
import json
import re
import unicodedata


class GlobalAnswer:
    def __init__(self, sources, *, cpu_guard=False):
        self.sources = sources
        self.keys = [str(source['citation']) for source in sources]
        self.buffer = ''
        self.rows = {}
        self.cpu_guard=cpu_guard
        self.grounding_fallbacks=set()
        self.schema = {'type': 'object', 'properties': {key: {'type': 'string', 'minLength': 1} for key in self.keys},
                       'required': self.keys, 'additionalProperties': False}

    @staticmethod
    def normalized(text):
        text=unicodedata.normalize('NFKC',text).casefold().replace('_',' ')
        digits={char:index for index,char in enumerate('零一二三四五六七八九')}
        def generation(match):
            word=match.group(0)
            if '十' in word:
                left,right=word.split('十',1)
                return str(digits.get(left,1)*10+digits.get(right,0))
            return str(digits.get(word,0))
        return re.sub(r'[零一二三四五六七八九十]+(?=代)',generation,text)

    def grounded_value(self, source, value):
        if not self.cpu_guard or not source.get('text'):return value
        evidence=self.normalized(source['text']);answer=self.normalized(value)
        numbers=lambda text:set(re.findall(r'\d+(?:[.,]\d+)?',text))
        sockets=lambda text:{re.sub(r'\s+','',item) for item in re.findall(r'\b(?:lga|am|fm|tr)\s*\d+\b|\bsocket\s*\d+\b',text)}
        expected,claimed=sockets(evidence),sockets(answer)
        wrong_numbers=bool(numbers(answer)-numbers(evidence))
        wrong_socket=bool(claimed-expected) or bool(expected and re.search(r'\b(?:socket|zócalo)\b',answer) and not claimed)
        false_absence=bool(re.search(r'\b(?:pentium|celeron|ryzen|cyrix|xeon)\b',evidence)
            and re.search(r'no (?:se )?(?:especifica|menciona|contiene|aporta)',answer))
        if wrong_numbers or wrong_socket or false_absence:
            self.grounding_fallbacks.add(str(source['citation']))
            text=re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]',' ',source['text'])
            text=re.sub(r'(?m)^\s*#{1,6}\s*','',text).replace('**','')
            return 'Extracto del documento: '+text
        return value

    def instruction(self):
        return ('\n\nFORMATO DE SALIDA: devuelve exclusivamente un objeto JSON con una propiedad por fuente. '
                'La clave "1" corresponde solo a la fuente [1], "2" a la [2], etc. '
                'Cada valor es una respuesta breve en español a la pregunta, usando únicamente el fragmento '
                'de esa fuente. Conserva generaciones, familias y condiciones de BIOS. '
                'No escribas nombres de archivo, tablas Markdown ni citas en los valores: los añade la aplicación. '
                'Si esa fuente no aporta el dato solicitado, indícalo en su valor, sin deducirlo de otros documentos. '
                'Cumple este esquema JSON: ' + json.dumps(self.schema, ensure_ascii=False))

    def feed(self, piece):
        """Publish only completely decoded string values, never raw JSON or half escapes."""
        self.buffer += piece
        decoder = json.JSONDecoder()
        text = self.buffer.lstrip()
        if not text.startswith('{'):
            return ''
        position = 1
        while position < len(text):
            while position < len(text) and (text[position].isspace() or text[position] == ','):
                position += 1
            if position >= len(text) or text[position] == '}':
                break
            try:
                key, position = decoder.raw_decode(text, position)
                while position < len(text) and text[position].isspace():
                    position += 1
                if position >= len(text) or text[position] != ':':
                    break
                position += 1
                while position < len(text) and text[position].isspace():
                    position += 1
                value, position = decoder.raw_decode(text, position)
            except json.JSONDecodeError:
                break
            if key not in self.keys or not isinstance(value, str):
                raise ValueError('El modelo devolvió una respuesta global con fuentes o datos no válidos.')
            self.rows[key] = value
        return self.render()

    def finish(self, answer):
        try:
            rows = json.loads(answer)
        except (ValueError, TypeError) as error:
            raise ValueError('La respuesta global está incompleta. No se ha confirmado como una respuesta de toda la biblioteca.') from error
        if (not isinstance(rows, dict) or set(rows) != set(self.keys)
                or any(not isinstance(value, str) or not value.strip() for value in rows.values())):
            raise ValueError('El modelo omitió documentos en la respuesta global. El resultado parcial no se ha confirmado como completo.')
        self.rows = rows
        return self.render()

    def render(self):
        self.grounding_fallbacks=set()
        def cell(value):
            return re.sub(r'([\\`*{}\[\]()#+.!_|<>])', r'\\\1', value).replace('\r', '').replace('\n', ' ')
        lines = ['| Documento | Respuesta |', '| --- | --- |']
        for source in self.sources:
            key = str(source['citation'])
            if key in self.rows:
                lines.append(f'| {cell(source["file"])} [{key}] | {cell(self.grounded_value(source,self.rows[key]))} |')
        return '\n'.join(lines) if self.rows else ''
