# Rediseño de RAG Local — reconocimiento y ejecución

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
