# Desarrollo, CI y diagnóstico

## Integración continua

El workflow [CI](https://github.com/tunelko/rag-lab/actions/workflows/ci.yml) se
ejecuta en cada push y pull request, y permite ejecución manual. Tiene una matriz
Windows/Linux con Python 3.13 y Node 24:

1. Instala PyTorch CPU y las dependencias fijadas del RAG, wizard y QA.
2. Comprueba dependencias con `pip check`.
3. Ejecuta `python -m unittest discover -s tests -t . -v`: persistencia SQLite real, vectores
   deterministas, modelos/sensores falsos, configuración y navegación del wizard.
4. Ejecuta los tests de gráficas y comprueba sintaxis JS y Bash.
5. En Linux instala Edge y ejecuta `python -m tests.browser.qa_redesign`; usa un servidor y corpus de prueba propios.
6. Conserva registros/capturas como artefactos durante siete días, incluso si falla.

No hay LLMs ni embeddings reales en las pruebas. CUDA queda oculta y el Hub HF en
modo offline. Pip y la instalación del navegador sí acceden a Internet. Las pruebas
no miden la calidad de respuestas ni garantizan compatibilidad de cualquier GPU.
Las Actions están fijadas a commits oficiales y el token de Actions solo tiene
`contents: read`. El workflow no necesita credenciales personales, Ollama ni HF_TOKEN.

Para reproducir las pruebas completas sin añadir paquetes al entorno del RAG,
usa un entorno de desarrollo propio con las dependencias CPU y las de
`requirements/wizard.txt` y `requirements/dev.txt`. Desde ese entorno:

```text
python -m unittest discover -s tests -t . -v
node --test tests/frontend/*.test.mjs
python -m playwright install msedge
python -m tests.browser.qa_redesign
```

La QA abre y cierra su servidor en el puerto 8767. Usa `--port` si está ocupado.
Para inspeccionar tu servidor existente sin generar respuestas: `python -m tests.browser.qa_web`.

Referencias: [checkout](https://github.com/actions/checkout),
[setup-python](https://github.com/actions/setup-python),
[Textual testing](https://textual.textualize.io/guide/testing/) y
[Playwright, navegadores](https://playwright.dev/python/docs/browsers).

## Instrumentación local

Estadísticas y Consola permiten distinguir los costes:

| Campo | Qué mide |
|---|---|
| `queue_seconds` | Espera real desde el encolado hasta el inicio del trabajo, con reloj monotónico. |
| `elapsed_seconds` | Ejecución del trabajo, sin sumar la cola. |
| `embedding_setup_seconds` | Obtención o preparación local del objeto de embeddings. |
| `vector_search_seconds` | Recuperación de fragmentos en el índice. |
| `source_preparation_seconds` | Construcción del contexto y referencias. |
| `routing_seconds` | Consulta de metadatos Ollama, selección de modelo y preparación de las imágenes adjuntas. |
| `generation_wall_seconds` | Tiempo de pared de la petición de generación completa. |
| `generation_tok_s` | Métrica nativa de generación de Ollama; no se infiere contando palabras. |

Las consultas completadas muestran la espera en la celda de tiempo cuando supera
un segundo. El detalle de fases aparece al pasar el cursor. API: `/api/statistics`
y `/api/jobs/{id}`. Las métricas se conservan en memoria, hasta 200 trabajos.

Cada trabajo terminado produce una línea `rag.performance · job_finished` con
identificador, tipo, estado, cola, ejecución y tiempos por fase. Ese registro no
incluye preguntas, fragmentos o credenciales. Los registros de otras partes de la
aplicación pueden incluir nombres de documentos; revisa lo que compartes al depurar.
No hay exportadores externos ni telemetría enviada a un servicio de monitorización.

La cola sigue siendo secuencial: instrumentarla no permite responder mientras
se indexa un lote. El siguiente paso de concurrencia requiere programar pausas
entre páginas y evaluar el cambio de modelos en VRAM; no debe confundirse con
añadir más hilos indiscriminadamente.

## Optimización de la biblioteca

`file_fingerprints.py` calcula SHA-256 leyendo un flujo. La ingesta usa este cálculo
directo cada vez; la comprobación de cambios al indexar conserva la lectura del
contenido. La consulta periódica del estado usa una caché independiente en memoria,
limitada a 512 archivos, para reutilizar huellas de documentos sin cambios.

La firma incluye dispositivo, inode, tamaño, modificación y cambio de metadatos
en nanosegundos. Una modificación o sustitución normal invalida la entrada. Si el
archivo cambia durante la lectura, se repite; después de dos cambios se devuelve
un error recuperable. Esta caché es una optimización de estado, no una comprobación
forense contra modificaciones que falsifiquen todos los metadatos. No se aplica a
la integridad de la ingesta ni se persiste en disco.

`/api/statistics` incluye contadores `diagnostics.fingerprints`: entradas, límite,
aciertos y fallos. Reiniciar el servidor deja la caché vacía. El tamaño del contexto,
la cuantización y la configuración GPU del modelo permanecen en sus ajustes previos.

Para medir esta parte con datos sintéticos, sin usar GPU, documentos del usuario,
red o modelos:

```text
python benchmarks/benchmark_local.py --output reports/local-benchmark.json
```

El script crea doce archivos temporales de unos 4 MiB, compara siete rondas de
huellas con/sin caché y mide 3000 selecciones de modelo con metadatos falsos.
Publica medianas, contador de aciertos y tiempo medio del enrutador. Los resultados
dependen del sistema, disco y carga; no son tokens/s ni una evaluación semántica.
El enrutador ya usa expresiones compiladas y reglas locales; se mantiene su
comportamiento y se mide antes de plantear cambios.

## Aplicar cambios al servidor local

Editar código y subir commits no reinicia el servidor que tengas abierto. Cuando
termine su trabajo, ciérralo con Ctrl+C y vuelve a iniciarlo para cargar los cambios
Python. Los documentos, vectores, modelos y preferencias conservan su ubicación.
Las actualizaciones JS se reciben al recargar la web.
