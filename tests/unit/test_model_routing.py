"""Routing decisions are local, deterministic, and never run models."""
from types import SimpleNamespace
import unittest

from langchain_core.documents import Document

from model_routing import select_model
from settings_store import Preferences


class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.settings = Preferences(response_model="text", vision_model="vision", multimodal=True)
        self.available = {"models": ["text", "vision"], "default_model": "text"}
        self.text = [(Document(page_content="Garantía de dos años.", metadata={"page": 1}), .1)]
        self.image = [(Document(page_content="Descripción visual", metadata={"asset_id": "a", "page": 1}), .1)]

    def route(self, question, hits=None, images=True, **kwargs):
        return select_model(self.settings, question, self.text if hits is None else hits, images, self.available,
                            lambda model: ["vision"] if model == "vision" else [], **kwargs)

    def test_text_question_does_not_attach_incidental_page_images(self):
        route = self.route("¿Cuánto dura la garantía?")
        self.assertEqual(route.model, "text")
        self.assertFalse(route.images)
        self.assertEqual(route.mode, "automatic")

    def test_visual_question_selects_vision_with_real_images(self):
        for question in ("¿Qué muestra el gráfico?", "Explica esta imagen", "Where is the connector?", "Describe the chart"):
            with self.subTest(question=question):
                route = self.route(question)
                self.assertEqual(route.model, "vision")
                self.assertTrue(route.images)

    def test_primary_visual_evidence_selects_vision_without_keyword(self):
        route = self.route("¿Cuántos puertos hay?", self.image)
        self.assertEqual(route.model, "vision")

    def test_text_only_instruction_overrides_visual_hits(self):
        for question in ("Responde solo con texto, sin usar las imágenes", "No uses la imagen", "Answer without images"):
            route = self.route(question, self.image)
            self.assertEqual(route.model, "text")
            self.assertFalse(route.images)

    def test_visual_question_without_retrieved_image_stays_textual(self):
        route = self.route("Analiza la figura", images=False)
        self.assertEqual(route.model, "text")
        self.assertIn("No hay imágenes originales", route.reason)

    def test_missing_vision_falls_back_to_text_without_guessing_another_model(self):
        self.available["models"] = ["text"]
        route = self.route("Analiza la figura")
        self.assertEqual(route.model, "text")
        self.assertFalse(route.images)
        self.assertIn("no está disponible", route.reason)

    def test_disabled_multimodal_uses_text(self):
        self.settings.multimodal = False
        self.assertEqual(self.route("Analiza la figura").model, "text")

    def test_manual_and_explicit_requests_take_precedence(self):
        self.settings.response_mode = "manual"
        route = self.route("Analiza la figura")
        self.assertEqual(route.model, "text")
        self.assertEqual(route.mode, "manual")
        explicit = self.route("Analiza la figura", requested="vision")
        self.assertEqual(explicit.model, "vision")
        self.assertEqual(explicit.mode, "explicit")
        self.assertTrue(explicit.images)
        self.assertFalse(self.route("No uses la imagen", requested="vision").images)

    def test_short_follow_up_keeps_previous_visual_intent(self):
        route = self.route("¿Y ese de la derecha?", history=[SimpleNamespace(role="user", content="Explica el diagrama")])
        self.assertEqual(route.model, "vision")

    def test_missing_manual_model_is_not_silently_replaced(self):
        self.settings.response_mode = "manual"
        self.settings.response_model = "missing"
        with self.assertRaises(ValueError):
            self.route("Garantía")


if __name__ == "__main__":
    unittest.main()
