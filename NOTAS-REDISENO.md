# Rediseño de RAG Local — reconocimiento y ejecución

## Ampliación solicitada después del rediseño (10-11-2026)

Las nuevas instrucciones del usuario sustituyen la distribución original: Chat limpio,
fuentes plegables, Configuración general y Estadísticas separadas. También autorizan
extender la ingesta y la generación a documentos multimodales. Las restricciones de
paleta, fuentes locales, accesibilidad y ejecución local del paquete se mantienen.

Corrección para manuales largos: el límite visual pasa a admitir 500 vistas y el lector
cuenta las vistas antes de solicitar descripciones, para evitar un fallo tardío tras
varias inferencias. El límite de trabajo deja de formar parte de la identidad del
parser; se reconocen los manifiestos antiguos para conservar documentos completos.
El despliegue en un servidor iniciado con la validación anterior requiere reiniciarlo
cuando termine su cola. Sus tareas ya iniciadas conservan su instantánea de ajustes.

- Nuevas rutas SPA: `/configuracion`, `/estadisticas`; las anteriores siguen funcionando.
- Consola: `/consola` y `/?vista=consola`, `GET /api/console?after=N&limit=200`.
  `server_console.py` captura stdout/stderr y registros Python con un búfer de 1000
  líneas, cursor incremental e identificación del proceso. La UI ofrece filtros,
  pausa, copia y descarga sin ejecución de comandos. El lector de archivos/API de
  compatibilidad permite usar la sección durante una indexación de la instancia
  anterior sin reiniciarla; se detiene al estar disponible la captura integrada.
- `GET/PUT /api/settings`: preferencias persistentes y validación de modelos locales.
  `response_mode` admite `automatic` (predeterminado) o `manual`. `response_model`
  es el candidato textual en automático y el modelo fijo en manual; `vision_model`
  sirve para indexar y para responder cuando la selección necesita visión.
- `GET /api/statistics`: estadísticas derivadas de tareas reales conservadas en memoria.
- `GET /api/telemetry`: CPU/RAM mediante psutil y lecturas GPU mediante consultas
  de nvidia-smi, con caché de 2 segundos. La instancia anterior puede entregar estas
  lecturas a través del lector local, manteniendo la indexación en marcha. Porcentaje
  de CPU por proceso normalizado al total de procesadores lógicos del equipo.
  Historial acotado a 15 minutos con muestras reales. Gráficas SVG locales compactas,
  ampliación en diálogo nativo con restauración de foco, escalas fijas, pausado de
  la visualización y navegación por teclado. El cambio de ventana y la ampliación
  no incrementan las consultas a los sensores ni detienen la indexación.
- `GET /api/figures/{sha256}`: imágenes PNG normalizadas, sin rutas arbitrarias.
- `POST /api/query` mantiene sus parámetros y añade historial acotado. Si se omiten
  modelo/k/search_only, aplica las preferencias compartidas. Los clientes antiguos
  pueden seguir especificándolos. Las fuentes añaden tipo y referencias visuales.
  Un `model` explícito mantiene prioridad sobre la selección automática. La selección
  de `model_routing.py` usa reglas locales y las fuentes, sin inferencia previa;
  `metrics.routing_mode/routing_reason/model` documentan la decisión real en Estadísticas.
- `rag.ingest` añade lector, extensiones y política opcionales; el lector original
  sigue siendo el predeterminado. La política evita omitir PDFs que ahora necesitan
  extracción visual. Se conserva ChromaDB y el modelo/dimensión de embeddings.
- `multimodal.py` implementa extracción PDF/imágenes e importación MinerU v1/v2;
  `settings_store.py` guarda preferencias de forma atómica. No ejecutan modelos al importar.
- `dist/chat.js` gestiona conversación y fuentes por respuesta; `workspace.css`
  organiza las nuevas secciones sobre los tokens existentes. Sin npm, CDN ni hosting.
- `dist/markdown.js` interpreta respuestas con Marked y sanea a un fragmento DOM
  mediante DOMPurify. Las librerías, versiones y licencias están en `dist/vendor`.
  Solo se insertan nodos saneados; las citas, botones de copia y tablas adaptables
  se construyen con DOM seguro. El cambio es frontend y presenta también respuestas
  anteriores del historial, sin solicitar nuevas inferencias ni reiniciar el servidor.
- Pruebas de parsing/almacenamiento real con CPU e inferencias simuladas en
  `test_multimodal.py`; QA de navegador ampliada a las cuatro secciones y miniaturas.

La descripción del primer rediseño que sigue es un registro del estado de partida;
el comportamiento actual y los límites multimodales están documentados en README.

## Material obligatorio leído

- `rag-local-redesign/PROMPT.md`, `README.md` y `spec/comportamiento.md`, completos.
- `design/tokens.css` y `design/fonts.css`: fuente de verdad visual.
- Referencias: Consulta, Biblioteca y diálogos de éxito/error del paquete.

## Stack y mapa

- FastAPI + Uvicorn, servidor ligado a `127.0.0.1`; sin framework frontend.
- Ambas pantallas: `dist/index.html`, `dist/app.css`, `dist/app.js`.
- Backend web: `web_app.py`; pipeline compartido: `rag.py`.
- Rutas existentes: `/` (Consulta) y `/admin` (Administración, renombrada Biblioteca).
- Modelo, chunking, embeddings CPU, recuperación y generación conservan su configuración.
- La carpeta no era un repositorio Git; se inicia un historial local, excluyendo cachés,
  entorno virtual, `.env`, corpus, índices e informes mediante `.gitignore`.

## API encontrada y huecos

| Endpoint actual | Contrato / observación |
| --- | --- |
| `GET /api/session` | Token CSRF del proceso. |
| `GET /api/status` | Biblioteca, configuración, modelo Ollama y trabajo activo. |
| `GET /api/jobs/{id}` | Fase textual, resultado, fuentes y métricas; sin porcentaje ni cancelación. |
| `POST /api/admin/upload` | Multipart `files[]`, `auto_index`; admite lotes, sin progreso individual de procesado. |
| `POST /api/admin/index` | Indexación de toda la biblioteca. |
| `POST /api/admin/prepare` | Preparación explícita de embeddings CPU. |
| `POST /api/query` | Pregunta, modelo, k, search_only; devuelve job_id. |
| `GET /api/source?file=...` | Descarga del original dentro del directorio de documentos. |

No hay SSE/WebSocket: se usará sondeo de trabajos activos cada segundo.
Faltan trabajos en cola simultáneos a las subidas, progreso, códigos de error,
recuperación al recargar y cancelación cooperativa.

## Plan concreto

1. Guardar el estado inicial en Git y copiar tokens/fuentes del paquete a `dist/design`.
2. Extender de forma aditiva los trabajos y la observación de la ingesta: porcentaje por
   fase, errores estructurados, consulta de trabajos activos y cancelación cooperativa.
3. Rehacer las dos pantallas con los componentes y tokens del paquete.
4. Implementar cola XHR (dos subidas), filtros, modales accesibles y estados de consulta.
5. Probar regresión de API/pipeline, errores, cancelación, teclado, red exclusivamente
   local, persistencia al recargar y diseño a 390 px. No ejecutar LLMs en las pruebas.

## Contrato aditivo implementado

- `/`, `/admin` se conservan; `/biblioteca` es un alias adicional. La navegación entre
  vistas se hace con History API para conservar los archivos en memoria y las subidas.
- `POST /api/admin/upload` conserva `files`, `auto_index`, `saved`, `job_id`.
  La UI envía **un archivo por XHR**, con un máximo de dos XHR simultáneos, y añade
  `batch_id`. La respuesta añade `document_id`. Cada trabajo individual incluye nombre,
  tamaño y lote para su recuperación. La validación antigua de lotes sigue disponible.
- `POST /api/admin/index` sigue aceptando una petición sin cuerpo. Opcionalmente acepta
  `{ "file": "nombre-relativo.txt" }` para reintentar o indexar un archivo ya subido.
- `GET /api/jobs?active=1` devuelve `jobs` activos, `recent` terminados y `active_job`.
- `DELETE /api/jobs/{id}` cancela un trabajo en cola o solicita cancelación cooperativa.
  Durante la escritura del índice responde 409 y la UI no ofrece ese control.
- `GET /api/jobs/{id}` conserva `status=queued|running|done|error` y el campo de error
  textual. Añade `cancelled`, el alias `state=processing` para `running`, `stage`,
  `progress`, `stage_progress`, `chunks`, `document_name`, `document_names`, `documents`,
  `error_info={code,message,detail}`, `cancel_requested` y `cancellable`.
- `GET /api/status` añade actividad reciente y trabajos; cada documento incluye estado,
  error estructurado, fecha de modificación, identificador y progreso si está en curso.
- Errores estructurados de validación en la subida conservan `detail` y añaden
  `error_info` con `FILE_TOO_LARGE`, `UNSUPPORTED_TYPE` o `INVALID_NAME`.
- `NO_TEXT` se expone como error web tras observar una ingesta sin fragmentos. No se
  cambian el algoritmo ni las entradas del manifiesto generadas por la ingesta anterior.

### Qué significa el progreso

La subida usa los eventos **reales** de `XMLHttpRequest.upload.onprogress`. El procesado
usa observadores opcionales: lectura/fragmentación es una operación atómica (0 o 100 %);
la indexación avanza por lotes de hasta 64 fragmentos realmente embebidos y guardados.
No hay porcentajes basados en tiempo ni avance aleatorio. `progress` representa el
procesado completo (mitad fragmentación, mitad índice); `stage_progress`, la fase actual.
El total pondera tamaños y conserva el máximo mostrado, incluso tras cancelar o añadir.
La estimación de tiempo usa una ventana de 3 s y no aparece sin 2 s de muestras.

La cancelación ocurre en puntos seguros: antes de modificar el índice o al recibir
el siguiente paquete del streaming. Una operación nativa ya iniciada (por ejemplo,
descarga/carga del embedding o lectura de un PDF) termina antes de observar la señal.
La UI muestra «Cancelando…» hasta la confirmación; no promete rollback de archivos.

### Recuperación y límites documentados

Los trabajos permanecen en el servidor durante su ejecución y se recuperan al recargar
la pestaña; no se promete reanudación después de reiniciar el proceso del servidor.
Los objetos File del navegador no sobreviven a una recarga: se conserva el metadato y
se muestra «Interrumpido» y «Volver a añadir» cuando no hay un trabajo servidor asociado.
No hay OCR instalado; se indica la necesidad de OCR sin ofrecer una función ficticia.

El foco de los diálogos vuelve al iniciador si sigue visible y habilitado. Si al terminar
el lote el botón de subida ya está deshabilitado por no quedar archivos, vuelve a la
navegación activa, evitando perderlo en body. Los diálogos nativos atrapan el foco y
admiten Esc; solo el de éxito se cierra al pulsar el fondo.

La relevancia usa `clamp(1 - distancia_coseno, 0, 1)`, con dos decimales. Es una similitud
recortada, **no una probabilidad de que la respuesta sea correcta**. No se altera el ranking.

### Activos, privacidad y versiones

Los CSS y WOFF2 se copian sin modificación desde `rag-local-redesign/design` a
`dist/design`, incluidas licencias. No hay CDNs, fuentes remotas, analítica ni dependencias
frontend nuevas. La única conexión de la interfaz es su servidor 127.0.0.1. La preparación
explícita de un embedding todavía puede descargar sus pesos desde el backend, como antes;
ningún documento se envía para indexarlo fuera del equipo.

El historial local usa autor automático `Codex <codex@localhost>` por comando porque no
había identidad Git configurada. No se modifica la configuración global de Git.

## Cómo probar

```powershell
.\.venv\Scripts\python.exe -m unittest -v test_rag test_web test_ollama_stream test_redesign
.\.venv\Scripts\python.exe qa_redesign.py
```

La segunda orden arranca su propio servidor de pruebas en 127.0.0.1:8767 y lo cierra al
acabar. Usa corpus aislados, vectores deterministas y un adaptador de generación ficticio:
**no usa Ollama, GPU ni modelos descargados**. Las subidas XHR y los lotes de Chroma sí
son reales. Capturas e informes en `reports/redesign-*`; los datos de respuestas y sus
métricas en esas capturas son pruebas, no benchmarks. `qa_web.py --url ...` hace solo
comprobaciones de lectura contra un servidor local ya iniciado.

## Estado

Implementación terminada. Pruebas de navegador completas: estados vacíos, seis archivos
(cuatro subidos/indexados y dos rechazados antes del servidor), total monótono, dos XHR
concurrentes con porcentaje intermedio real, pausa/aborto/continuación, subida sin indexar,
indexación posterior, modales y foco, preparación, consulta y Ctrl+Enter, citas, resaltado,
copiar, búsqueda sin generación, error/reintento en línea, detener, recuperación al recargar,
nombres hostiles literales, paginación de 20, filtros, búsqueda con debounce, 390 px,
estadísticas apiladas, movimiento reducido y ausencia de peticiones externas/errores JS.

Informe: `reports/redesign-qa.json`. Capturas comparadas con las cuatro maquetas del paquete.
Las 20 pruebas unitarias y de integración han pasado. JavaScript y Python sin errores
de sintaxis; `git diff --check` correcto. Los activos copiados se han comparado por SHA-256
con los originales. Los tests se ejecutan sin descargar ni ejecutar modelos reales.
