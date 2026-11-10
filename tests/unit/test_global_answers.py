"""Ground output rows in fixed file/citation identities across streamed JSON chunks."""
import json
import unittest

from global_answers import GlobalAnswer


class GlobalAnswerTests(unittest.TestCase):
    def test_english_table_keeps_original_filenames_and_source_excerpt(self):
        answer = GlobalAnswer([{'citation': 1, 'file': 'placa_original.pdf',
                    'text': 'CPU: Intel Core 12th generation, LGA1700.'}], cpu_guard=True, language='en')
        self.assertIn('inglés', answer.instruction())
        rendered = answer.finish('{"1":"Intel Core 99th generation, LGA9999."}')
        self.assertIn('| Document | Answer |', rendered)
        self.assertIn('placa', rendered)
        self.assertIn('Source excerpt: CPU: Intel Core 12th generation', rendered)
        self.assertIn('[1]', rendered)
        answer = GlobalAnswer([{'citation': 1, 'file': 'manual.pdf',
                    'text': 'CPU: Celeron and Pentium, Socket 370.'}], cpu_guard=True, language='en')
        rendered = answer.finish('{"1":"No processor information is provided."}')
        self.assertIn('Source excerpt: CPU: Celeron and Pentium', rendered)

    def test_streaming_preserves_file_ids_and_escapes_partial_content(self):
        answer=GlobalAnswer([{'citation':1,'file':'board_A|x.pdf'}, {'citation':2,'file':'other.pdf'}])
        payload=json.dumps({'1':'Intel Core "12"\nCPU | socket', '2':'No aporta el dato.'})
        for character in payload:
            partial=answer.feed(character)
            self.assertNotIn('"1":',partial)
        rendered=answer.finish(payload)
        self.assertIn('board\\_A\\|x\\.pdf [1]',rendered)
        self.assertIn('other\\.pdf [2]',rendered)
        self.assertIn('Intel Core "12" CPU \\| socket',rendered)

    def test_missing_files_foreign_ids_and_truncated_answers_are_rejected(self):
        answer=GlobalAnswer([{'citation':1,'file':'first.pdf'}, {'citation':2,'file':'second.pdf'}])
        for payload in ('{"1":"dato"}', '{"1":"dato","99":"dato"}', '{"1":"dato","2":false}', '{"1":"dato",'):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                answer.finish(payload)

    def test_cpu_numbers_and_socket_claims_use_literal_evidence_when_the_model_mixes_boards(self):
        sources=[{'citation':1,'file':'z790.pdf','text':'CPU: Intel Core 14/13/12. Socket LGA1700.'},
            {'citation':2,'file':'old.pdf','text':'CPU: Socket 370. Intel Pentium and Celeron, FC-PGA.'}]
        answer=GlobalAnswer(sources,cpu_guard=True)
        rendered=answer.finish(json.dumps({'1':'Intel Core generation 6/7, socket LGA1151.',
            '2':'Intel Pentium in_socket FC-PGA.'}))
        self.assertNotIn('LGA1151',rendered)
        self.assertIn('LGA1700',rendered)
        self.assertIn('Socket 370',rendered)
        self.assertEqual(answer.grounding_fallbacks,{'1','2'})
        chinese=GlobalAnswer([{'citation':1,'file':'manual.pdf','text':'CPU 第十四代/第十三代/第十二代 LGA1700'}],cpu_guard=True)
        self.assertNotIn('Extracto del documento',chinese.finish('{"1":"Intel Core 14/13/12, LGA1700."}'))
