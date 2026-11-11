"""The generic record engine with the cybersecurity profile: question text only, no data or model.

Two properties are tested separately. A plan is a faithful reading of the *whole* question
(conditions, order, limit, grouping). Anything the engine does not fully understand yields
a Rejection or no plan at all, never a partial plan.
"""
import unittest

import record_engine
from record_engine import Plan, Rejection
import record_profiles

TODAY = "2026-11-11"
VOCABULARY = {
    "microsoft": ["exchange server", "windows"], "cisco": ["ios xe"], "linux": ["kernel"], "android": ["kernel"],
    "google": ["chromium v8"], "palo alto networks": ["pan-os"], "fortinet": ["fortios"], "citrix": ["netscaler adc"],
    "apple": ["ios", "macos"],
}
RECORD_SET = record_profiles.load("ciberseguridad")["record_sets"][0]


def plan(question, **options):
    return record_engine.plan(question, RECORD_SET, today=TODAY, vocabulary=VOCABULARY, **options)


def conditions(question):
    result = plan(question)
    assert isinstance(result, Plan), (question, result)
    return {(item.field, item.op, item.value) for item in result.conditions}


class LookupTests(unittest.TestCase):
    def test_spelling_variants_become_one_canonical_identifier(self):
        for question in ("CVE-2021-44228", "what is cve-2021-44228", "detalles de CVE 2021 44228",
                         "la CVE‑2021‑44228 y su parche", "cve_2021_44228", "Resume CVE—2021—44228"):
            self.assertEqual(plan(question), Plan("vulnerabilities", "lookup", ids=("CVE-2021-44228",)), question)

    def test_several_identifiers_keep_their_order_and_win_over_everything_else(self):
        self.assertEqual(plan("CVE-2019-0708 vs CVE-2017-0144, y otra vez CVE-2019-0708").ids, ("CVE-2019-0708", "CVE-2017-0144"))
        self.assertEqual(plan("¿La CVE-2023-27997 de Fortinet tiene explotación conocida y qué parche la corrige?").mode, "lookup")

    def test_malformed_identifiers_are_not_identifiers(self):
        for question in ("¿Qué significa el código de error CVE-12 del instalador?", "Part number CWE1234-B specifications",
                         "CVE-1899-1234", "cve-2024-12"):
            self.assertNotIsInstance(plan(question), Plan, question)

    def test_outside_the_record_category_only_an_identifier_uses_the_records(self):
        self.assertIsNone(plan("Vulnerabilidades críticas con explotación conocida", lookup_only=True))
        self.assertEqual(plan("¿Qué es CVE-2022-0847?", lookup_only=True).mode, "lookup")


class ConditionTests(unittest.TestCase):
    def test_flags_numbers_identifiers_and_enumerated_values(self):
        cases = {
            "Vulnerabilidades con explotación conocida": {("kev", "is", True)},
            "Muéstrame solo vulnerabilidades incluidas en KEV": {("kev", "is", True)},
            "Lista vulnerabilidades asociadas a campañas de ransomware": {("ransomware", "is", True)},
            "Vulnerabilidades con EPSS superior al 90 %": {("epss", "gt", 0.9)},
            "Vulnerabilidades de Google Chromium con EPSS mayor o igual a 0.6":
                {("epss", "ge", 0.6), ("vendor", "eq", "google"), ("product", "contains", "chromium")},
            "Muéstrame las vulnerabilidades con EPSS igual o superior a 0.8": {("epss", "ge", 0.8)},
            "Vulnerabilidades con EPSS entre 0,2 y 0,6 publicadas desde 2022":
                {("epss", "ge", 0.2), ("epss", "le", 0.6), ("published", "ge", "2022-01-01")},
            "Busca vulnerabilidades con CVSS de 9 o más": {("cvss", "ge", 9.0)},
            "Vulnerabilidades con CVSS entre 7.0 y 8.9": {("cvss", "ge", 7.0), ("cvss", "le", 8.9)},
            "Busca CWE-787 en Linux Kernel con CVSS de al menos 8":
                {("cwe", "in", ("CWE-787",)), ("cvss", "ge", 8.0), ("vendor", "eq", "linux"), ("product", "contains", "kernel")},
            "Vulnerabilidades con CVSS no superior a 5": {("cvss", "le", 5.0)},
            "Vulnerabilidades con EPSS mayor que 0,5 y CVSS menor que 7": {("epss", "gt", 0.5), ("cvss", "lt", 7.0)},
            "Vulnerabilidades por encima del percentil 95 de EPSS": {("percentile", "gt", 0.95)},
            "CVEs en KEV con percentil EPSS inferior a 10": {("kev", "is", True), ("percentile", "lt", 0.1)},
            "Vulnerabilidades críticas con explotación conocida": {("severity", "eq", "critical"), ("kev", "is", True)},
            "Vulnerabilidades de Debian de severidad alta": {("ecosystem", "eq", "Debian"), ("severity", "eq", "high")},
            "Known exploited vulnerabilities classified as CWE-416 or cwe 125": {("kev", "is", True), ("cwe", "in", ("CWE-416", "CWE-125"))},
            "Vulnerabilidades del ecosistema npm": {("ecosystem", "eq", "npm")},
        }
        for question, expected in cases.items():
            self.assertEqual(conditions(question), expected, question)

    def test_dates_bind_to_the_field_that_is_named(self):
        cases = {
            "Vulnerabilidades añadidas a KEV en 2024": {("kev", "is", True), ("kev_added", "ge", "2024-01-01"), ("kev_added", "le", "2024-12-31")},
            "CVE publicadas en 2023 con explotación conocida": {("kev", "is", True), ("published", "ge", "2023-01-01"), ("published", "le", "2023-12-31")},
            "Vulnerabilidades añadidas a KEV en los últimos 30 días": {("kev", "is", True), ("kev_added", "ge", "2026-10-12"), ("kev_added", "le", TODAY)},
            "KEV entries added this year": {("kev", "is", True), ("kev_added", "ge", "2026-01-01"), ("kev_added", "le", TODAY)},
            "Vulnerabilidades publicadas desde el 1 de enero de 2025": {("published", "ge", "2025-01-01")},
            "Busca vulnerabilidades publicadas entre 2024-03-01 y 2024-06-30": {("published", "ge", "2024-03-01"), ("published", "le", "2024-06-30")},
            "Lista vulnerabilidades publicadas antes del 1 de enero de 2020": {("published", "le", "2019-12-31")},
            "Vulnerabilidades añadidas a KEV después de marzo de 2025": {("kev", "is", True), ("kev_added", "ge", "2025-04-01")},
            "¿Qué vulnerabilidades de Citrix se añadieron a KEV entre enero y junio de 2023?":
                {("kev", "is", True), ("vendor", "eq", "citrix"), ("kev_added", "ge", "2023-01-01"), ("kev_added", "le", "2023-06-30")},
            "Vulnerabilidades publicadas antes de 2020 y añadidas a KEV desde 2024":
                {("kev", "is", True), ("published", "le", "2019-12-31"), ("kev_added", "ge", "2024-01-01")},
        }
        for question, expected in cases.items():
            self.assertEqual(conditions(question), expected, question)

    def test_names_come_from_the_published_vocabulary(self):
        cases = {
            "Known exploited vulnerabilities in Cisco products": {("kev", "is", True), ("vendor", "eq", "cisco")},
            "Vulnerabilidades del proveedor Microsoft": {("vendor", "eq", "microsoft")},
            "Busca vulnerabilidades de Cisco IOS XE": {("vendor", "eq", "cisco"), ("product", "contains", "ios xe")},
            "Vulnerabilidades del kernel de Linux con explotación conocida":
                {("kev", "is", True), ("vendor", "eq", "linux"), ("product", "contains", "kernel")},
            # A beginning that only one vendor has, and a product only one vendor makes.
            "Enséñame las vulnerabilidades de Palo Alto PAN-OS": {("vendor", "eq", "palo alto networks"), ("product", "contains", "pan-os")},
            "Lista los fallos que afectan a Windows": {("vendor", "eq", "microsoft"), ("product", "contains", "windows")},
            # A product several vendors make stays a product condition only.
            "Vulnerabilidades del kernel en KEV": {("kev", "is", True), ("product", "contains", "kernel")},
        }
        for question, expected in cases.items():
            self.assertEqual(conditions(question), expected, question)

    def test_negation_is_read_or_refused_never_ignored(self):
        self.assertEqual(conditions("Vulnerabilidades en KEV que no se usan en ransomware"), {("kev", "is", True), ("ransomware", "is", False)})
        for question in ("Known exploited vulnerabilities not from Microsoft", "Vulnerabilidades en KEV excepto las de Cisco",
                         "Vulnerabilidades que no son críticas en KEV", "Vulnerabilidades en KEV sin parche"):
            self.assertIsInstance(plan(question), Rejection, question)
        # Vendor and product only exist for catalogued entries: "not in KEV" cannot be combined with them.
        rejection = plan("Busca vulnerabilidades de Exchange que no estén en KEV")
        self.assertEqual((rejection.reason, rejection.items[0]), ("combination", "kev"))


class OrderAndAggregateTests(unittest.TestCase):
    def test_ranking_and_limits(self):
        result = plan("Las 10 vulnerabilidades con mayor EPSS")
        self.assertEqual((result.order, result.limit, result.conditions), (("epss", "desc"), 10, ()))
        result = plan("Top 5 de PAN-OS por probabilidad EPSS")
        self.assertEqual((result.order, result.limit), (("epss", "desc"), 5))
        result = plan("Vulnerabilidades en KEV ordenadas por CVSS ascendente, las 5 primeras")
        self.assertEqual((result.order, result.limit), (("cvss", "asc"), 5))
        # "Recent" orders by the date that fits the conditions; it never invents a window.
        self.assertEqual(plan("Vulnerabilidades recientes con explotación conocida").order, ("kev_added", "desc"))
        result = plan("Dame las vulnerabilidades publicadas en los últimos 30 días, primero las más recientes")
        self.assertEqual(result.order, ("published", "desc"))
        self.assertEqual(plan("Las 20 vulnerabilidades más recientes de Cisco").limit, 20)

    def test_counts_and_groupings(self):
        cases = {
            "¿Cuántas vulnerabilidades hay en total?": ("count",),
            "Cuenta las vulnerabilidades KEV de Microsoft": ("count",),
            "How many CVEs have an EPSS score above 0.9?": ("count",),
            "Agrupa las vulnerabilidades por proveedor": ("group", "vendor"),
            "Agrupa por producto las vulnerabilidades de Microsoft": ("group", "product"),
            "Agrupa por ecosistema las vulnerabilidades con CVSS de 9 o más": ("group", "ecosystem"),
            "Most common CWEs among known exploited vulnerabilities": ("group", "cwe"),
            "¿Qué fabricantes tienen más vulnerabilidades con explotación conocida?": ("group", "vendor"),
            "Número de vulnerabilidades añadidas a KEV por año": ("group", "kev_added:year"),
            "Distribuye las altas de KEV por año": ("group", "kev_added:year"),
            "Agrupa las vulnerabilidades publicadas por mes": ("group", "published:month"),
            "Evolución mensual de las altas en KEV durante 2024": ("group", "kev_added:month"),
        }
        for question, expected in cases.items():
            result = plan(question)
            self.assertIsInstance(result, Plan, question)
            self.assertEqual((result.mode, result.aggregate), ("aggregate", expected), question)
        self.assertEqual(conditions("Cuenta las vulnerabilidades KEV de Microsoft"), {("kev", "is", True), ("vendor", "eq", "microsoft")})


class ClosedCoverageTests(unittest.TestCase):
    def test_a_word_that_is_not_understood_stops_the_plan(self):
        cases = {
            "Vulnerabilidades modificadas desde el 15 de octubre de 2026": "modificadas",
            "Vulnerabilidades en KEV con parche disponible": "parche",
            "Vulnerabilidades de ejecución remota de código en KEV": "remota",
            "Vulnerabilidades de corrupción de memoria explotadas de Google Chromium": "corrupcion",
            "Vulnerabilidades explotadas de Microsft": "microsft",
            "Vulnerabilidades en KEV descubiertas por Google": "descubiertas",
        }
        for question, word in cases.items():
            result = plan(question)
            self.assertIsInstance(result, Rejection, question)
            self.assertIn(word, result.items, question)
        self.assertIn("microsoft", plan("Vulnerabilidades explotadas de Microsft").suggestions)

    def test_out_of_range_values_and_undated_dates_are_refused(self):
        self.assertEqual(plan("Top 500 vulnerabilidades por EPSS").reason, "range")
        self.assertEqual(plan("Vulnerabilidades con CVSS mayor que 15").reason, "range")
        # Two date fields and none named: the engine does not pick one.
        rejection = plan("Vulnerabilidades en KEV de 2024")
        self.assertEqual((rejection.reason, set(rejection.items)), ("date", {"published", "kev_added"}))

    def test_questions_that_state_nothing_exact_have_no_plan(self):
        for question in ("¿Cómo configuro el escáner para importar vulnerabilidades?", "Cuéntame algo interesante sobre seguridad",
                         "Explícame qué significa la puntuación CVSS", "¿Qué vulnerabilidades debería mirar?",
                         "¿Cómo funciona un ataque de use-after-free?", "¿Qué diferencia hay entre CVSS y EPSS?",
                         "Advertencias críticas de seguridad al instalar la CPU"):
            self.assertNotIsInstance(plan(question), Plan, question)

    def test_the_applied_conditions_are_stated_by_the_engine(self):
        line = record_engine.describe(plan("Las 3 vulnerabilidades de Microsoft en KEV con mayor EPSS"), RECORD_SET, "es")
        self.assertEqual(line, "Condiciones aplicadas: en el catálogo KEV: sí; fabricante (según KEV) = microsoft"
                               " · Orden: EPSS, descendente · Límite: 3")
        line = record_engine.describe(plan("How many known exploited vulnerabilities were added in 2025?"), RECORD_SET, "en")
        self.assertEqual(line, "Conditions applied: date added to KEV ≥ 2025-01-01; date added to KEV ≤ 2025-12-31;"
                               " in the KEV catalogue: yes · Count")


class ProfileTests(unittest.TestCase):
    def test_sql_names_in_a_profile_must_be_plain_identifiers(self):
        import copy
        profile = copy.deepcopy(record_profiles.load("ciberseguridad"))
        profile["record_sets"][0]["fields"]["cvss"]["column"] = "v.cvss_base; DROP TABLE kev"
        with self.assertRaises(record_profiles.ProfileError):
            record_profiles.validate(profile)
        profile = copy.deepcopy(record_profiles.load("ciberseguridad"))
        profile["record_sets"][0]["tables"]["k"]["view"] = "current_kev UNION SELECT"
        with self.assertRaises(record_profiles.ProfileError):
            record_profiles.validate(profile)

    def test_no_profile_is_active_unless_configured(self):
        import os
        from unittest.mock import patch
        with patch.dict(os.environ, {"RAG_PROFILES": ""}):
            self.assertEqual(record_profiles.active(), [])
            self.assertEqual(record_profiles.record_set("records:vulnerabilities"), (None, None))
        with patch.dict(os.environ, {"RAG_PROFILES": "ciberseguridad"}):
            profile, record_set = record_profiles.record_set("records:vulnerabilities")
            self.assertEqual((profile["id"], record_set["id"]), ("ciberseguridad", "vulnerabilities"))


if __name__ == "__main__":
    unittest.main()
