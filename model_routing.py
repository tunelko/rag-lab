"""Choose among configured local models without running a classifier or an LLM."""
from dataclasses import dataclass
import re
import unicodedata


def normalize(text):
    return "".join(char for char in unicodedata.normalize("NFD", text.casefold()) if not unicodedata.combining(char))


VISUAL = re.compile(r"\b(imagen(?:es)?|figuras?|grafic[oa]s?|diagramas?|fotos?|fotografias?|esquemas?|capturas?|mapas?|planos?|curvas?|ejes?|pinout|colores?|images?|pictures?|figures?|graphs?|charts?|diagrams?|photos?|screenshots?|plots?|schematics?|colou?rs?)\b")
TEXT_ONLY = re.compile(r"\b(solo (?:el |con |a partir del )?texto|unicamente (?:el |con )?texto|sin (?:usar |ver |analizar |consultar )?(?:las? )?imagen(?:es)?|no (?:uses|mires|analices) (?:las? )?imagen(?:es)?|text only|without images?)\b")
LOCATION = re.compile(r"\b(donde|ubicacion|localiza|situad[oa]|where)\b.*\b(conector|puerto|ranura|pin|connector|port|slot)\b")


def asks_for_vision(question, history=()):
    text = normalize(question)
    if TEXT_ONLY.search(text):
        return False
    if VISUAL.search(text) or LOCATION.search(text):
        return True
    # Carry visual intent into short follow-ups such as "¿Y ese de la derecha?".
    if len(text) < 160 and re.search(r"\b(eso|ese|esa|este|esta|ahi|derecha|izquierda|arriba|abajo|that|this)\b", text):
        previous = next((message.content for message in reversed(history) if message.role == "user"), "")
        return bool(VISUAL.search(normalize(previous)))
    return False


@dataclass(frozen=True)
class Route:
    model: str
    images: bool
    mode: str
    reason: str


def select_model(settings, question, hits, has_images, available, capabilities, requested=None, history=(), input_images=False):
    names = available["models"]
    text_model = settings.response_model or available["default_model"]
    if input_images:
        model = requested or (text_model if settings.response_mode == 'manual' else settings.vision_model)
        if model not in names or 'vision' not in capabilities(model):
            raise ValueError('La imagen adjunta necesita un modelo con visión instalado. Revísalo en Configuración.')
        return Route(model, True, 'explicit' if requested else settings.response_mode,
                     'La consulta incluye una imagen del usuario; se usa un modelo con visión.')
    if requested or settings.response_mode == "manual":
        model = requested or text_model
        if model not in names:
            raise ValueError("El modelo de respuesta elegido no está instalado. Revísalo en Configuración.")
        images = bool(settings.multimodal and has_images and not TEXT_ONLY.search(normalize(question)) and "vision" in capabilities(model))
        return Route(model, images, "explicit" if requested else "manual",
                     "Modelo fijado por la petición." if requested else "Modelo fijado en Configuración.")
    if text_model not in names:
        text_model = available["default_model"]
    if text_model not in names:
        raise ValueError("No hay un modelo local de respuesta disponible en Ollama.")
    text_only = bool(TEXT_ONLY.search(normalize(question)))
    visual_question = not text_only and asks_for_vision(question, history)
    visual_evidence = bool(hits and hits[0][0].metadata.get("asset_id"))
    needs_vision = not text_only and (visual_question or visual_evidence)
    if not settings.multimodal:
        return Route(text_model, False, "automatic", "Procesamiento multimodal desactivado; se usa el modelo de texto.")
    if text_only:
        return Route(text_model, False, "automatic", "La petición solicita usar solo texto; no se adjuntan imágenes.")
    if not has_images:
        reason = "No hay imágenes originales asociadas a las fuentes recuperadas; se usa la evidencia textual." if needs_vision else "Las fuentes recuperadas permiten una consulta de texto."
        return Route(text_model, False, "automatic", reason)
    if not needs_vision:
        return Route(text_model, False, "automatic", "La pregunta y la evidencia principal son textuales; no se adjuntan imágenes.")
    vision = settings.vision_model
    if vision in names and "vision" in capabilities(vision):
        reason = "La pregunta requiere inspección visual y hay imágenes relacionadas." if visual_question else "La evidencia principal recuperada es una descripción visual; se comprueba su imagen original."
        return Route(vision, True, "automatic", reason)
    return Route(text_model, False, "automatic", "El modelo visual configurado no está disponible; se responde con la evidencia textual recuperada.")


def select_external_model(settings, question, hits, has_images, history=(), input_images=False):
    """Same intent rules; cloud vision uses its explicitly selected model."""
    text_only = bool(TEXT_ONLY.search(normalize(question)))
    visual_evidence = bool(hits and hits[0][0].metadata.get('asset_id'))
    use_images = input_images or (settings.response_mode == 'automatic' and settings.multimodal
        and has_images and not text_only and (asks_for_vision(question, history) or visual_evidence))
    if use_images:
        if not settings.external_vision_model:
            raise ValueError('Selecciona un modelo visual del proveedor en Configuración para consultar imágenes.')
        return Route(settings.external_vision_model, True, 'external_vision',
                     'Consulta visual: imágenes enviadas al modelo visual del proveedor configurado.')
    if not settings.external_model:
        raise ValueError('Selecciona un modelo de respuesta del proveedor en Configuración.')
    return Route(settings.external_model, False, 'external', 'Respuesta textual mediante el proveedor configurado.')
