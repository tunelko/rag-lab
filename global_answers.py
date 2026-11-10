"""Validate one answer per retrieved file; the app owns document names and citations."""
import json
import re


class GlobalAnswer:
    def __init__(self, sources):
        self.sources = sources
        self.keys = [str(source['citation']) for source in sources]
        self.buffer = ''
        self.rows = {}
        self.schema = {'type': 'object', 'properties': {key: {'type': 'string', 'minLength': 1} for key in self.keys},
                       'required': self.keys, 'additionalProperties': False}

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
        def cell(value):
            return re.sub(r'([\\`*{}\[\]()#+.!_|<>])', r'\\\1', value).replace('\r', '').replace('\n', ' ')
        lines = ['| Documento | Respuesta |', '| --- | --- |']
        for source in self.sources:
            key = str(source['citation'])
            if key in self.rows:
                lines.append(f'| {cell(source["file"])} [{key}] | {cell(self.rows[key])} |')
        return '\n'.join(lines) if self.rows else ''
