"""Public tariff conversion and pagination, entirely offline."""

import io
import json
import unittest
import urllib.error

from provider_pricing import fetch_pricing, PricingError


def product(model, kind, nanos, *, mode="realtime", unit="token", size=1000, region="fr-par"):
    return {"sku": f"/{model}/{kind}/{mode}/{region}", "product": model,
            "properties": {"generative_apis": {"token_type": kind, "consumption_mode": mode}},
            "locality": {"region": region}, "unit_of_measure": {"unit": unit, "size": size},
            "price": {"retail_price": {"currency_code": "EUR", "units": 0, "nanos": nanos}}}


class Catalog:
    def __init__(self, pages):
        self.pages, self.requests = iter(pages), []

    def open(self, request, timeout):
        self.requests.append(request)
        return io.BytesIO(json.dumps(next(self.pages)).encode())


class PricingTests(unittest.TestCase):
    def test_pages_convert_units_and_exclude_batch_and_other_regions(self):
        first = [product('model', 'input_token', 1800000),
                 product('model', 'input_token', 900000, mode='batch'),
                 product('model', 'output_token', 7000000, region='nl-ams')]
        second = [product('model', 'output_token', 5500000),
                  product('model', 'input_cached_token', 120000),
                  product('audio', 'input_audio', 3000000, unit='second', size=60)]
        client = Catalog([{'products': first, 'total_count': 6}, {'products': second, 'total_count': 6}])
        result = fetch_pricing('scaleway', opener=client)
        self.assertEqual(float(result['models']['model']['input']['amount']), 1.8)
        self.assertEqual(float(result['models']['model']['output']['amount']), 5.5)
        self.assertEqual(float(result['models']['model']['cached_input']['amount']), .12)
        self.assertEqual(result['models']['audio']['input']['unit'], 'audio_minute')
        self.assertEqual(float(result['models']['audio']['input']['amount']), .003)
        self.assertNotIn('output', result['models']['audio'])
        self.assertEqual(len(client.requests), 2)
        self.assertIn('page=2', client.requests[-1].full_url)
        self.assertTrue(all(r.get_header('Authorization') is None for r in client.requests))
        self.assertFalse(result['tax_included'])
        self.assertTrue(result['retrieved_at'].endswith('+00:00'))

    def test_missing_invalid_and_zero_prices_are_distinct(self):
        products = [product('free', 'output_token', 0), product('negative', 'input_token', -1),
                    product('zero-size', 'input_token', 1, size=0), product('nonfinite', 'input_token', 'NaN'),
                    product('missing', 'input_token', 2), product('unsupported-unit', 'input_token', 2, unit='hour')]
        products[-2].pop('price')
        result = fetch_pricing('scaleway', opener=Catalog([{'products': products, 'total_count': len(products)}]))
        self.assertEqual(set(result['models']), {'free'})
        self.assertEqual(float(result['models']['free']['output']['amount']), 0)

    def test_conflicting_prices_are_not_silently_selected(self):
        first, second = product('model', 'input_token', 1), product('model', 'input_token', 2)
        second['sku'] += '-other'
        result = fetch_pricing('scaleway', opener=Catalog([{'products': [first, second], 'total_count': 2}]))
        self.assertNotIn('input', result['models']['model'])

    def test_each_refresh_fetches_current_values(self):
        client = Catalog([{'products': [product('model', 'input_token', n)], 'total_count': 1}
                          for n in (100000, 200000)])
        first, second = [fetch_pricing('scaleway', opener=client) for _ in range(2)]
        self.assertNotEqual(first['models'], second['models'])
        self.assertEqual(len(client.requests), 2)

    def test_unsupported_provider_does_not_contact_any_endpoint(self):
        client = Catalog([])
        self.assertFalse(fetch_pricing('ovh', opener=client)['supported'])
        self.assertEqual(client.requests, [])

    def test_incomplete_or_failed_fetch_is_an_error_not_a_free_price(self):
        for payload in ({'products': [], 'total_count': 3}, {'products': None}, {'products': [], 'total_count': 2001}):
            with self.subTest(payload=payload), self.assertRaises(PricingError):
                fetch_pricing('scaleway', opener=Catalog([payload]))
        class Failed:
            def open(self, *args, **kwargs):
                raise urllib.error.URLError('offline')
        with self.assertRaises(PricingError):
            fetch_pricing('scaleway', opener=Failed())
