# Interface languages

The UI ships with Spanish and English. The language selector is available in the
top bar on every page, including mobile layouts.
The resolved language uses a locally served Spain/UK flag in the top-right selector.
Fonts are local too; a browser on a private HTTP LAN requires no external resources.

- **Browser language** chooses the first supported entry in `navigator.languages`,
  including regional variants such as `es-MX` and `en-GB`. Unsupported languages
  fall back to English.
- Selecting Spanish or English overrides detection. The choice is stored under
  `rag:language` in this browser's local storage. It is separate from shared RAG
  settings and does not change another browser's preference.
- Switching language updates the current UI without submitting a query, uploading
  files, cancelling work, resetting a queue or discarding an unsaved question.
  Number/date formatting and accessibility labels follow the selected language.
  Configuration edits save automatically; switching language does not write them.

## Content and local generation

New chat requests send `language: "es"` or `"en"` to `/api/query`. The backend
instructs the selected local Ollama model to answer in that language, including
global document tables. Catalog and retrieval-only replies are localized locally.
Existing API clients default to Spanish when they omit the field.

This is a generation instruction, not an external translation service or a
guarantee that every model will obey. No translation API, API key, cloud inference
or extra model download is required. Models are not loaded just to change language.

Original documents, extracted text, visual captions, source excerpts, filenames,
existing chat answers and raw server logs retain their original contents. Known
application status/error messages are translated for display; unfamiliar technical
diagnostics are preserved. Grounding fallbacks deliberately quote source text
rather than translating it without evidence.

## Translation files

`dist/i18n.js` loads `dist/locales/es.json` and `en.json` from the local server.
Catalogs use stable message keys and named `{placeholders}`. Insert translations
as plain text with `textContent` or translated attributes; never use them as HTML.
The HTML uses `data-i18n`, `data-i18n-placeholder`, `data-i18n-title` and
`data-i18n-aria-label`. JavaScript uses `t(key, values)`.

All placeholders must be retained, although translators may reorder them. A missing
English key uses its Spanish value. If one catalog cannot be loaded, the remaining
catalog keeps the UI usable. Missing keys in both catalogs are detected by tests.

When adding a language, supply a matching JSON catalog, add its code to the browser
resolver and the API language validation, and extend the backend generation and
catalog strings. Run the frontend tests, language API tests and browser QA. Check
long labels and keyboard navigation at mobile widths.

Browser QA covers detection, switching, persistence, unavailable-catalog fallback,
unsaved form contents and the absence of mutating requests during language changes.
