# Visual selection audit

This is a CPU-only evaluation tool. It does **not** change ingestion policy, call
Ollama, load an embedding model, rewrite PDFs or touch the document/vector index.
The current production visual policy remains in place until the proposed selection
has been reviewed.

The classifier records native-text length, image area and placement, vector path
counts, edge decoration, detected-table area and technical-diagram cues. It keeps
large raster images, low-text graphics, complex vectors and ambiguous structures.
Pinout cues such as `ATX_12V`, `CPU_FAN`, `F_PANEL` and `JUSB1` preserve vector-only
technical diagrams. Plain text, native tables and small edge decoration can be
proposed for exclusion. Scores are heuristics, not calibrated probabilities.

The rules use [PyMuPDF page inspection](https://pymupdf.readthedocs.io/en/latest/page.html).
Table detection is imperfect; ambiguous cases stay candidates for review.

Run from the project root, repeating `--pdf` for three representative manuals:

```powershell
.\.venv\Scripts\python.exe -m tools.visual_audit --pdf "manual-text.pdf" --pdf "manual-bios.pdf" --pdf "manual-diagrams.pdf" --output reports/visual-audit --previews 36
```

On Linux use `.venv/bin/python`. Open `reports/visual-audit/index.html` locally.
The report shows a stratified sample across manuals and decision reasons. Label
each page **Requiere visión**, **Texto / tabla suficiente** or **Dudosa**, and export
`visual-labels.json`. Source PDFs and generated previews remain private local files.

Evaluate the saved labels without parsing the PDFs again:

```text
python -m tools.visual_audit --evaluate reports/visual-audit/features.json --labels visual-labels.json
```

Recall and precision apply only to reviewed pages; unlabeled/doubtful pages are
excluded. Inspect every false negative, especially connector layouts and electrical
schematics. A stratified sample is not an unbiased estimate of whole-library recall.
Stable PDF hashes/page IDs let the same human labels evaluate later rule revisions.

The report's reduction compares **candidate pages**, assuming one description per
candidate. It does not measure actual model calls, cache hits or elapsed ingestion
time saved. No target reduction percentage is imposed. Use the persistent indexing
history and resource charts for a subsequent controlled ingestion comparison.
