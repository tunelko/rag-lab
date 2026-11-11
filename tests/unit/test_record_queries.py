"""The structured-question planner reads only the question text: no data, model or network."""
import unittest

import record_queries
from library_queries import query_intent

TODAY = "2026-11-11"
CATALOG = {
    "microsoft": ["exchange server", "windows"], "cisco": ["ios xe"], "linux": ["kernel"], "android": ["kernel"],
    "google": ["chromium v8"], "palo alto networks": ["pan-os"], "fortinet": ["fortios"], "citrix": ["netscaler adc"],
}


def plan(question):
    result = record_queries.plan(question, today=TODAY, vendors=CATALOG)
    return result.as_dict() if result else None


class IdentifierTests(unittest.TestCase):
    def test_spelling_variants_become_one_canonical_identifier(self):
        for question in ("CVE-2021-44228", "what is cve-2021-44228", "detalles de CVE 2021 44228",
                         "la CVE‑2021‑44228 y su parche", "cve_2021_44228"):
            self.assertEqual(plan(question), {"mode": "record_lookup", "ids": ["CVE-2021-44228"]}, question)

    def test_several_identifiers_keep_their_order_without_repeats(self):
        self.assertEqual(plan("CVE-2019-0708 vs CVE-2017-0144, y otra vez CVE-2019-0708")["ids"],
                         ["CVE-2019-0708", "CVE-2017-0144"])

    def test_an_identifier_wins_over_filter_words(self):
        self.assertEqual(plan("¿La CVE-2023-27997 de Fortinet tiene explotación conocida y uso en ransomware?"),
                         {"mode": "record_lookup", "ids": ["CVE-2023-27997"]})

    def test_malformed_identifiers_are_not_identifiers(self):
        for question in ("¿Qué significa el código de error CVE-12 del instalador?", "Part number CWE1234-B specifications",
                         "CVE-1899-1234", "cve-2024-12"):
            self.assertIsNone(plan(question), question)


class FilterTests(unittest.TestCase):
    def test_exact_conditions_are_extracted_without_invented_values(self):
        cases = {
            "Vulnerabilidades con explotación conocida": {"kev": True},
            "Vulnerabilidades en KEV usadas en campañas de ransomware": {"kev": True, "ransomware": True},
            "Vulnerabilidades con EPSS superior al 90 %": {"epss_min": 0.9},
            "CVEs with an EPSS score above 0.5 that are in KEV": {"kev": True, "epss_min": 0.5},
            "Vulnerabilidades con EPSS entre 0,2 y 0,6 publicadas desde 2022":
                {"epss_min": 0.2, "epss_max": 0.6, "published_from": "2022-01-01"},
            "Vulnerabilidades con CVSS de 9 o más": {"cvss_min": 9.0},
            "Vulnerabilidades críticas con explotación conocida": {"kev": True, "severity": "critical"},
            "Known exploited vulnerabilities classified as CWE-416 or cwe 125": {"kev": True, "cwe": ["CWE-416", "CWE-125"]},
            "Vulnerabilidades que afectan a Ubuntu con explotación conocida": {"kev": True, "ecosystem": "Ubuntu"},
        }
        for question, expected in cases.items():
            self.assertEqual(plan(question), {"mode": "record_filter", "filter": expected}, question)

    def test_a_percentile_threshold_is_not_a_score_threshold(self):
        self.assertEqual(plan("Vulnerabilidades por encima del percentil 95 de EPSS")["filter"], {"percentile_min": 0.95})
        self.assertEqual(plan("CVEs en KEV con percentil EPSS inferior a 10")["filter"], {"kev": True, "percentile_max": 0.1})

    def test_dates_use_the_named_field_and_the_given_today(self):
        cases = {
            "Vulnerabilidades añadidas a KEV en 2024": {"kev": True, "kev_added_from": "2024-01-01", "kev_added_to": "2024-12-31"},
            "CVE publicadas en 2023 con explotación conocida": {"kev": True, "published_from": "2023-01-01", "published_to": "2023-12-31"},
            "Vulnerabilidades añadidas a KEV en los últimos 30 días": {"kev": True, "kev_added_from": "2026-10-12", "kev_added_to": TODAY},
            "KEV entries added this year": {"kev": True, "kev_added_from": "2026-01-01", "kev_added_to": TODAY},
            "¿Qué vulnerabilidades de Citrix se añadieron a KEV entre enero y junio de 2023?":
                {"kev": True, "vendor": "citrix", "kev_added_from": "2023-01-01", "kev_added_to": "2023-06-30"},
            "Vulnerabilidades críticas publicadas antes de 2022 con explotación conocida":
                {"kev": True, "severity": "critical", "published_to": "2021-12-31"},
        }
        for question, expected in cases.items():
            self.assertEqual(plan(question)["filter"], expected, question)

    def test_vendor_and_product_come_from_the_vocabulary(self):
        cases = {
            "Known exploited vulnerabilities in Cisco products": {"kev": True, "vendor": "cisco"},
            "Known exploited vulnerabilities in Microsoft Exchange Server": {"kev": True, "vendor": "microsoft", "product": "exchange server"},
            "Vulnerabilidades del kernel de Linux con explotación conocida": {"kev": True, "vendor": "linux", "product": "kernel"},
            "known exploited vulnerabilities in Palo Alto Networks PAN-OS": {"kev": True, "vendor": "palo alto networks", "product": "pan-os"},
            # A product without its vendor resolves the vendor only when a single one makes it.
            "Vulnerabilidades de Windows con explotación conocida": {"kev": True, "vendor": "microsoft", "product": "windows"},
            "vulnerabilidades explotadas del kernel": {"kev": True, "product": "kernel"},
        }
        for question, expected in cases.items():
            self.assertEqual(plan(question)["filter"], expected, question)

    def test_recent_most_likely_and_top_are_ordering_not_thresholds(self):
        cases = {
            "Vulnerabilidades recientes con explotación conocida": {"kev": True, "sort": "kev_added", "order": "desc"},
            "Las 10 vulnerabilidades con mayor EPSS": {"sort": "epss", "order": "desc", "limit": 10},
            "Top 5 known exploited vulnerabilities by CVSS score": {"kev": True, "sort": "cvss", "order": "desc", "limit": 5},
        }
        for question, expected in cases.items():
            self.assertEqual(plan(question)["filter"], expected, question)

    def test_a_weakness_described_in_words_is_not_turned_into_a_cwe(self):
        self.assertEqual(plan("Vulnerabilidades de corrupción de memoria explotadas de Google Chromium")["filter"],
                         {"kev": True, "vendor": "google", "product": "chromium"})


class AggregateTests(unittest.TestCase):
    def test_counts_and_groupings_keep_their_filter(self):
        cases = {
            "¿Cuántas vulnerabilidades hay en KEV?": ({"kev": True}, {"op": "count"}),
            "How many known exploited vulnerabilities does Microsoft have?": ({"kev": True, "vendor": "microsoft"}, {"op": "count"}),
            "Número de vulnerabilidades añadidas a KEV por año": ({"kev": True}, {"op": "group", "by": "kev_added_year"}),
            "¿Qué fabricantes tienen más vulnerabilidades con explotación conocida?": ({"kev": True}, {"op": "group", "by": "vendor"}),
            "Most common CWEs among known exploited vulnerabilities": ({"kev": True}, {"op": "group", "by": "cwe"}),
            "Evolución mensual de las altas en KEV durante 2024":
                ({"kev": True, "kev_added_from": "2024-01-01", "kev_added_to": "2024-12-31"}, {"op": "group", "by": "kev_added_month"}),
        }
        for question, (filters, aggregate) in cases.items():
            self.assertEqual(plan(question), {"mode": "record_aggregate", "filter": filters, "aggregate": aggregate}, question)


    def test_a_grouping_or_count_over_everything_is_exact(self):
        cases = {
            "Agrupa vulnerabilidades por CWE": ({}, {"op": "group", "by": "cwe"}),
            "Agrupa las vulnerabilidades publicadas por mes": ({}, {"op": "group", "by": "published_month"}),
            "Distribuye las altas de KEV por año": ({"kev": True}, {"op": "group", "by": "kev_added_year"}),
        }
        for question, (filters, aggregate) in cases.items():
            self.assertEqual(plan(question), {"mode": "record_aggregate", "filter": filters, "aggregate": aggregate}, question)


class EcosystemTests(unittest.TestCase):
    def test_an_ecosystem_name_is_not_also_a_vendor_product(self):
        catalog = {"canonical": ["ubuntu"], "debian": ["debian"]}
        result = record_queries.plan("Vulnerabilidades del ecosistema Ubuntu", today=TODAY, vendors=catalog)
        self.assertEqual(result.as_dict(), {"mode": "record_filter", "filter": {"ecosystem": "Ubuntu"}})
        result = record_queries.plan("Known exploited vulnerabilities affecting Debian packages", today=TODAY, vendors=catalog)
        self.assertEqual(result.filter, {"kev": True, "ecosystem": "Debian"})


class ExistingPathsTests(unittest.TestCase):
    def test_document_questions_are_left_to_the_existing_classifier(self):
        cases = {
            "¿Cómo funciona un ataque de use-after-free?": "focused",
            "¿Qué diferencia hay entre CVSS y EPSS?": "focused",
            "What does the KEV catalog require federal agencies to do?": "focused",
            "Advertencias críticas de seguridad al instalar la CPU": "focused",
            "¿Qué versión de BIOS de 2023 añade soporte para Ryzen 5000?": "focused",
            "Top 5 pasos para actualizar la BIOS con M-Flash": "focused",
            "¿Qué vulnerabilidades corrige esta BIOS?": "focused",
            "¿Cuántos documentos hay en la biblioteca?": "catalog_count",
            "Lista los manuales indexados": "catalog_list",
            "Compara el chipset de todos los manuales": "global",
        }
        for question, mode in cases.items():
            self.assertIsNone(plan(question), question)
            self.assertEqual(query_intent(question).mode, mode, question)


if __name__ == "__main__":
    unittest.main()
