"""Untrusted query images are bounded and normalized without model execution."""
import base64
import io
import struct
import unittest
import zlib
from PIL import Image

from query_images import normalize_query_images


def picture():
    buffer=io.BytesIO();Image.new('RGB',(20,10),'white').save(buffer,format='PNG');return buffer.getvalue()


class QueryImageTests(unittest.TestCase):
    def test_png_data_uri_is_normalized_to_bounded_jpeg(self):
        encoded='data:image/png;base64,'+base64.b64encode(picture()).decode()
        output,=normalize_query_images([encoded],1)
        with Image.open(io.BytesIO(base64.b64decode(output))) as image:
            self.assertEqual(image.format,'JPEG');self.assertEqual(image.size,(20,10))

    def test_invalid_formats_counts_encoding_and_oversized_bytes_are_rejected(self):
        for images,limit in [(['data:image/svg+xml;base64,PHN2Zy8+'],1),(['invalid%%%'],1),
                             ([base64.b64encode(picture()).decode()]*2,1),
                             ([base64.b64encode(b'x'*(3*1024**2+1)).decode()],1)]:
            with self.assertRaises(ValueError):normalize_query_images(images,limit)

    def test_forged_large_dimensions_are_rejected_before_pixel_decoding(self):
        data=bytearray(picture());data[16:24]=struct.pack('>II',5000,4000);data[29:33]=struct.pack('>I',zlib.crc32(data[12:29]))
        with self.assertRaisesRegex(ValueError,'megapíxeles'):
            normalize_query_images([base64.b64encode(data).decode()],1)
