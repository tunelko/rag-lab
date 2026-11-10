# Tests

Run from the project root in an environment installed with
`requirements/torch-cpu.txt` followed by `requirements/dev.txt`.

| Suite | Command |
|---|---|
| Python / terminal wizard | `python -m unittest discover -s tests -t . -v` |
| JavaScript | `node --test tests/frontend/*.test.mjs` |
| Isolated browser acceptance | `python -m tests.browser.qa_redesign` |
| Read-only running-server check | `python -m tests.browser.qa_web --url http://127.0.0.1:8765` |

Browser tests require Edge: `python -m playwright install msedge`.
Linux can add `--with-deps` to install browser system libraries.

Unit tests use fake vectors/models/sensors and temporary files. Browser acceptance
starts and closes its own server/corpus (port 8767, configurable with `--port`) and
never calls Ollama. Reports go to ignored `reports/`. Test metrics are fixtures,
not GPU benchmarks.
