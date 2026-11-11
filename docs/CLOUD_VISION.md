# Cloud answers, local indexing

The answer provider selector applies only to chat, with text and vision model dropdowns.
The separate **Local indexing** section always uses Ollama (`vision_model`) for
figure descriptions and CPU for extraction/embeddings. It has no cloud credential
or cloud generation path. Local query vision uses `query_vision_model`, defaulting
to the existing local vision model for backward compatibility. Cloud query vision
uses `external_vision_model`. Only indexing settings affect parser/cache identity.
The vision list is the intersection of the live authenticated `/models` catalog
and confirmed image-input capabilities. Metadata declaring input modalities takes
precedence; where absent, exact IDs verified against provider documentation are
used (`provider_vision.py`). Unknown models are excluded from vision, not guessed
from family names. Refreshing models updates both lists without inference.

References checked 2026-11-11:

- [Groq vision](https://console.groq.com/docs/vision): Qwen3.8 has a three-image limit.
- [Scaleway supported models](https://www.scaleway.com/en/docs/generative-apis/reference-content/supported-models/).
- [OVHcloud catalog](https://www.ovhcloud.com/en/public-cloud/ai-endpoints/catalog/).
- [xAI Chat Completions image input](https://docs.x.ai/developers/model-capabilities/legacy/chat-completions).
- [xAI image understanding](https://docs.x.ai/developers/model-capabilities/images/understanding).

When extending exact IDs, verify image **input** via Chat Completions, add a test,
and retain catalog membership filtering. Image generation, OCR-only endpoints,
audio and embedding models are not substitutes for a vision chat model. Unknown
input modalities may require a catalog refresh after restarting the server.

Selecting a cloud answer vision model enables remote image processing only on
relevant chat queries. It does not start indexing or upload anything by itself.
There is no automatic selection, fallback to another provider, or background
reindexing. Existing local vision selection is preserved when switching providers;
the external model selections are cleared to avoid cross-provider mismatches.

During indexing, figures are normalized to PNG and described by local Ollama.
Existing local caption caches and parser identities stay compatible. Changing
the local indexing model marks affected documents for explicit reindexing; changing
the answer provider or answer vision model never requires reindexing. No existing
document, vector or caption is deleted by switching answer providers.

The temporary cloud-ingestion behavior introduced in `7f00b74` has been removed.
Existing local indexes become usable again without reindexing. Any cloud captions
cached during that version remain on disk but are not reused as local captions.

Automatic queries choose the answer model for text and the vision model when the
question/evidence needs images. Manual cloud mode keeps document queries textual;
an explicitly attached image uses the selected vision model. User attachments are
never silently dropped. Retrieved figures are capped at the model's supported
image count. Statistics record the actual provider, model, image count and route.

All cloud calls use fixed HTTPS endpoints, no redirects or ambient proxies, and
the application's User-Agent. Keys stay server-side. Image payloads use embedded
PNG/JPEG data, not URLs exposing local files. Cloud processing may incur charges;
the Settings disclosure describes the transfer. Tests use synthetic images and
fake HTTPS responses, not private documents, a GPU or billed inference.
