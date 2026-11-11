# Cloud vision

Settings uses one provider selector and separate answer/vision model dropdowns.
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

Selecting a cloud vision model explicitly enables remote image processing on
the next relevant job. It does not start indexing or upload anything by itself.
There is no automatic selection, fallback to another provider, or background
reindexing. Existing local vision selection is preserved when switching providers;
the external model selections are cleared to avoid cross-provider mismatches.

During indexing, CPU extraction and embeddings remain local. Selected figures
are normalized to PNG and sent as embedded data to the configured provider using
its existing encrypted credential. Complete captions are cached by provider and
model; incomplete/error/cancelled captions are not cached. Existing Ollama cache
and parser identities stay compatible. Changing the caption provider/model marks
affected documents for explicit reindexing, without deleting original documents.

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
