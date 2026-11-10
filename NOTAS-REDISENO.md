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

## Contrato nuevo (se actualizará con la implementación)

Se conservarán campos y rutas existentes. Se añadirán campos a los trabajos y endpoints
de listado/cancelación. La subida individual se realizará con el mismo endpoint y un
único archivo por petición. No se simulará ningún porcentaje de procesado.

## Estado

Reconocimiento terminado. Implementación en curso.
