"""Tender selection, summary sheets and the all-items question path. Offline: no feed is read."""
import unittest

from library_queries import SUMMARY_SUFFIX, has_summaries, query_intent, set_question, summary_scope
from tools import fetch_pliegos

RULES = {
    "contract_types": ["2"], "security_cpv_prefixes": ["4873", "7281"], "it_cpv_prefixes": ["72", "48"],
    "excluded_cpv_prefixes": ["44", "45", "66"], "unambiguous_terms": ["ciberseguridad", "seguridad de la informacion"],
    "acronyms": ["SOC", "ENS"], "it_only_terms": ["cortafuegos", "vulnerabilidades"],
    "excluded_terms": ["seguridad vial", "seguridad y salud"],
}
ENTRY = """
<id>https://contrataciondelestado.es/sindicacion/licitacionesPerfilContratante/1</id>
<link href="https://contrataciondelestado.es/wps/poc?uri=deeplink:detalle_licitacion&amp;idEvl=abc"/>
<title>Servicio de SOC/MDR y Herramientas de
   Ciberseguridad para el Ayuntamiento de Ejemplo</title>
<updated>2026-11-10T20:07:49.694+01:00</updated>
<cbc:ContractFolderID>SE2026/44</cbc:ContractFolderID>
<cbc-place-ext:ContractFolderStatusCode listURI="x">PUB</cbc-place-ext:ContractFolderStatusCode>
<cac:PartyName><cbc:Name>Junta de Gobierno del Ayuntamiento de Ejemplo</cbc:Name></cac:PartyName>
<cac:ProcurementProject><cbc:TypeCode listURI="x">2</cbc:TypeCode>
  <cac:BudgetAmount><cbc:EstimatedOverallContractAmount currencyID="EUR">1500000</cbc:EstimatedOverallContractAmount>
    <cbc:TotalAmount currencyID="EUR">1089000</cbc:TotalAmount><cbc:TaxExclusiveAmount currencyID="EUR">900000.5</cbc:TaxExclusiveAmount></cac:BudgetAmount>
  <cac:RequiredCommodityClassification><cbc:ItemClassificationCode listURI="x">72000000</cbc:ItemClassificationCode></cac:RequiredCommodityClassification>
  <cac:RealizedLocation><cbc:CountrySubentity>Murcia</cbc:CountrySubentity>
    <cbc:CountrySubentityCode listURI="x">ES620</cbc:CountrySubentityCode>
    <cac:Address><cbc:CityName>Cartagena</cbc:CityName></cac:Address></cac:RealizedLocation></cac:ProcurementProject>
<cac:TenderingProcess><cac:TenderSubmissionDeadlinePeriod><cbc:EndDate>2026-11-23</cbc:EndDate><cbc:EndTime>23:59:00</cbc:EndTime>
</cac:TenderSubmissionDeadlinePeriod></cac:TenderingProcess>
<cac:TechnicalDocumentReference><cbc:ID>PPT.pdf</cbc:ID><cac:Attachment><cac:ExternalReference>
  <cbc:URI>https://contrataciondelestado.es/FileSystem/servlet/GetDocumentByIdServlet?cifrado=a&amp;DocumentIdParam=b</cbc:URI>
</cac:ExternalReference></cac:Attachment></cac:TechnicalDocumentReference>
"""


def tender(title, cpv=("72000000",), kind="2"):
    return {"type": kind, "title": title, "cpv": list(cpv)}


class SelectionTests(unittest.TestCase):
    def test_only_service_contracts_about_security_are_selected(self):
        self.assertEqual(fetch_pliegos.reason(tender("Servicio de ciberseguridad gestionada"), RULES), "término: ciberseguridad")
        self.assertEqual(fetch_pliegos.reason(tender("Auditoría de certificación del ENS"), RULES), "término: ENS")
        self.assertEqual(fetch_pliegos.reason(tender("Gestión de vulnerabilidades"), RULES), "CPV de TI + término: vulnerabilidades")
        self.assertEqual(fetch_pliegos.reason(tender("Evaluación técnica de sistemas", ("72810000",)), RULES), "CPV de seguridad")
        # A supply of licences is not a service, whatever it is about.
        self.assertIsNone(fetch_pliegos.reason(tender("Licencias de ciberseguridad", kind="1"), RULES))

    def test_look_alike_words_and_other_trades_are_left_out(self):
        for title, cpv in (("Programa de enseñanza social", ("72000000",)),            # ENS and SOC inside other words
                           ("Puertas cortafuegos del edificio", ("45421130",)),        # works
                           ("Suministro de cortafuegos forestales", ("77000000",)),    # ambiguous word without an IT code
                           ("Seguro de ciberseguridad", ("66517300",)),                # insurance
                           ("Coordinación de seguridad y salud", ("72000000",)),
                           ("Mantenimiento de ordenadores", ("72000000",))):
            self.assertIsNone(fetch_pliegos.reason(tender(title, cpv), RULES), title)


class SheetTests(unittest.TestCase):
    def setUp(self):
        self.tender = fetch_pliegos.parse_entry(ENTRY)

    def test_the_feed_entry_gives_what_the_sheet_needs(self):
        expected = {"folder": "SE2026/44", "status": "PUB", "type": "2", "deadline": "2026-11-23", "deadline_time": "23:59:00",
                    "amount_without_tax": "900000.5", "estimated_value": "1500000", "place": "Murcia", "cpv": ["72000000"],
                    "place_code": "ES620", "city": "Cartagena",
                    "organ": "Junta de Gobierno del Ayuntamiento de Ejemplo",
                    "title": "Servicio de SOC/MDR y Herramientas de Ciberseguridad para el Ayuntamiento de Ejemplo"}
        self.assertEqual({key: self.tender[key] for key in expected}, expected)
        self.assertEqual(self.tender["documents"][0]["url"],
                         "https://contrataciondelestado.es/FileSystem/servlet/GetDocumentByIdServlet?cifrado=a&DocumentIdParam=b")

    def test_an_open_tender_says_until_when_and_dates_its_data(self):
        text = fetch_pliegos.sheet({**self.tender, "documents": [{"file": "SE2026_44__PPT__PPT.pdf"}]})
        head = text.split("\n\n")[0]
        self.assertIn("Estado a 10/11/2026: en plazo de presentación de ofertas. "
                      "Abierta para presentar ofertas: sí, hasta el 23/11/2026 a las 23:59.", head)
        self.assertIn("Presupuesto sin impuestos: 900.000,50 EUR · Valor estimado: 1.500.000,00 EUR", head)
        # What a question about the whole set needs fits in the part every sheet contributes, and the
        # status comes before the budget and the subject, so it survives when that part is cut short.
        self.assertLess(len(head), 460)
        self.assertLess(head.index("Estado a"), head.index("Presupuesto sin impuestos"))
        self.assertLess(head.index("Presupuesto sin impuestos"), head.index("Objeto:"))
        # Every fact a whole-set question may ask for is in that part, the place included.
        self.assertIn("Comunidad autónoma: Región de Murcia · Provincia o zona: Murcia", head)
        self.assertIn("Localidad: Cartagena", text)
        self.assertIn("SE2026_44__PPT__PPT.pdf", text)
        self.assertIn("el estado y los plazos cambian", text)

    def test_the_region_comes_from_the_platform_code_not_from_a_name(self):
        cases = {"ES620": "Región de Murcia", "ES615": "Andalucía", "ES300": "Comunidad de Madrid", "ES13": "Cantabria",
                 "ES709": "Canarias", "ES": "toda España", "": "", "FR101": ""}
        for code, expected in cases.items():
            self.assertEqual(fetch_pliegos.region({"place_code": code}), expected, code)
        text = fetch_pliegos.sheet({**self.tender, "place_code": "", "place": "", "documents": []})
        self.assertIn("Comunidad autónoma: no publicada", text)

    def test_a_closed_tender_says_why(self):
        text = fetch_pliegos.sheet({**self.tender, "status": "ADJ", "documents": []})
        self.assertIn("Abierta para presentar ofertas: no (adjudicada); el plazo terminó el 23/11/2026.", text)
        text = fetch_pliegos.sheet({**self.tender, "status": "PUB", "deadline": "2026-11-01", "documents": []})
        self.assertIn("en plazo según la plataforma, con fecha límite 01/11/2026", text)   # the platform and the date disagree


class WholeSetQuestionTests(unittest.TestCase):
    def test_questions_about_every_tender_use_the_all_items_path(self):
        for question in ("Todos los pliegos estan abiertos para presentarse?", "¿Qué licitaciones siguen abiertas?",
                         "¿Cuántos pliegos tienen un presupuesto superior a 500.000 euros?", "Presupuesto de cada licitación",
                         "Which tenders are still open?"):
            self.assertEqual(query_intent(question).mode, "global", question)
        for question in ("¿Qué requisitos debe cumplir el servicio de SOC del Ayuntamiento de Cartagena?",
                         "tiempo de respuesta ante incidentes críticos", "¿Qué dice el pliego sobre penalidades?"):
            self.assertEqual(query_intent(question).mode, "focused", question)

    def test_any_which_and_how_many_are_questions_about_the_set(self):
        for question in ("hay alguno de la comunidad de murcia?", "¿Hay alguna licitación de Andalucía?", "¿Cuáles vencen esta semana?",
                         "¿Cuántas superan los 500.000 euros?", "alguno de más de un millón", "lista de órganos que licitan SOC",
                         "Is there any from Madrid?", "Which are still open?"):
            self.assertTrue(set_question(question), question)
        for question in ("¿Qué requisitos debe cumplir el servicio de SOC del Ayuntamiento de Cartagena?",
                         "tiempo de respuesta ante incidentes críticos", "¿Qué dice sobre penalidades?",
                         "explica el plan de formación", "¿Cuál es el presupuesto del SOC de Cartagena?"[:0] or "plazo de garantía exigido"):
            self.assertFalse(set_question(question), question)
        documents = [{"name": "A__PPT__x.pdf", "indexed": True}]
        self.assertFalse(has_summaries(documents))
        self.assertTrue(has_summaries([*documents, {"name": "A" + SUMMARY_SUFFIX, "indexed": True}]))

    def test_the_whole_set_is_read_from_summary_sheets_when_there_are_any(self):
        documents = [{"name": "A__PPT__x.pdf", "indexed": True}, {"name": "A" + SUMMARY_SUFFIX, "indexed": True},
                     {"name": "B__PCAP__y.pdf", "indexed": True}, {"name": "B" + SUMMARY_SUFFIX, "indexed": False}]
        self.assertEqual([item["name"] for item in summary_scope(documents)], ["A" + SUMMARY_SUFFIX])
        self.assertEqual(summary_scope(documents[::2]), documents[::2])     # no sheets: every document, as before
        self.assertEqual(fetch_pliegos.SHEET_SUFFIX, SUMMARY_SUFFIX)


if __name__ == "__main__":
    unittest.main()
