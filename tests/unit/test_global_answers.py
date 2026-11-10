"""Ground output rows in fixed file/citation identities across streamed JSON chunks."""
import json
import unittest

from global_answers import GlobalAnswer


class GlobalAnswerTests(unittest.TestCase):
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
