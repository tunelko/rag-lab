# Guía de implementación — Rediseño integral de la interfaz de RAG Local

## Rol y objetivo

Eres un ingeniero frontend sénior trabajando en el repositorio de **RAG Local**, una aplicación web que se ejecuta en el equipo del usuario (`http://127.0.0.1:8765`) para indexar documentos y consultarlos con un LLM servido por Ollama.

Tu tarea es **implementar el rediseño integral de sus dos pantallas** (Consulta y Administración, que pasa a llamarse **Biblioteca** en la interfaz) siguiendo el material de este paquete:

1. Nueva identidad visual: paleta orgánica y cálida ("Lino y arcilla") y tipografías modernas autoalojadas.
2. Mejor experiencia al tratar documentos: **cola de subida con porcentaje por archivo y total**, estados claros por fase (subida → fragmentos → índice), cancelación y reintento.
3. **Modales de éxito y de error** al terminar los procesos (subida/indexación, actualizar índice, preparar modelo de búsqueda).

No cambies el comportamiento del pipeline RAG (ingesta, fragmentación, embeddings, recuperación, generación). Solo puedes tocar el backend para **exponer progreso y errores estructurados** si hoy no existen (ver §6).

## Material del paquete

| Ruta | Qué es |
|---|---|
| `mockups/consulta.html` | Maqueta de referencia de la pantalla Consulta (estado con respuesta y fuentes). |
| `mockups/biblioteca.html` | Maqueta de Biblioteca/Administración con la cola de subida en curso. |
| `mockups/modal-exito.html`, `mockups/modal-error.html` | Modales de fin de proceso. |
| `design/tokens.css` | **Fuente de verdad** de colores, tipografía, radios, sombras y tiempos (variables CSS). |
| `design/fonts.css` + `design/fonts/*.woff2` | Bricolage Grotesque (titulares), Instrument Sans (interfaz), JetBrains Mono (datos técnicos). Variables, OFL 1.1. |
| `spec/comportamiento.md` | Máquina de estados de la cola, reglas de modales, textos exactos, accesibilidad y seguridad. **Léelo entero antes de programar.** |
| `referencia-actual/*.png` | Capturas de la interfaz actual, para que identifiques qué componente es cuál. |

Las maquetas usan estilos en línea por comodidad de diseño: **no copies el HTML tal cual**. Reprodúcelas con componentes y clases del proyecto y con las variables de `tokens.css` (ningún hex suelto en los estilos nuevos). Abre las maquetas en el navegador para comparar visualmente.

## Reglas no negociables

- **Cero dependencias externas en tiempo de ejecución.** La app presume de funcionar "en tu equipo": nada de Google Fonts, CDNs ni analítica. Copia `design/fonts/` y los CSS a la carpeta de estáticos del proyecto.
- **Usa el stack existente.** No introduzcas un framework nuevo (ni React, ni Tailwind, ni librerías de componentes) si el proyecto no los usa ya. Vanilla JS/CSS es perfectamente válido.
- **Conserva endpoints, rutas y nombres de campos existentes.** Si añades algo, que sea aditivo y retrocompatible.
- **Idioma de la interfaz: español de España**, con los textos de `spec/comportamiento.md`.
- **Accesibilidad AA**: contraste ya resuelto en los tokens; controles reales (`<button>`, `<input>`, `<label>`), objetivos táctiles ≥ 44 px, foco visible, `aria-*` donde se indica en la spec, `prefers-reduced-motion` respetado.
- **Seguridad**: nombres de archivo, fragmentos recuperados y respuestas del modelo son datos no confiables. Nunca los insertes con `innerHTML`; usa `textContent`/nodos DOM (detalles en la spec, §7).

## Plan de trabajo

### Paso 0 — Reconocimiento (antes de tocar nada)
1. Identifica el stack (servidor, plantillas, carpeta de estáticos, cómo se sirve el JS/CSS).
2. Localiza: plantillas de Consulta y Administración, CSS actual, JS de subida/indexación/consulta, y endpoints de subida, indexado, estado, listado de documentos, consulta y "preparar modelo".
3. Comprueba si el backend ya informa de **progreso por trabajo** (porcentaje o fase) y de **errores con código**. Esto decide si necesitas el §6.
4. Escribe `NOTAS-REDISENO.md` en la raíz con: stack detectado, mapa pantalla→archivos, endpoints encontrados, huecos detectados y el plan concreto. Mantenlo actualizado.

### Paso 1 — Fundaciones visuales
- Copia `design/` a los estáticos (p. ej. `static/design/`) y enlaza `fonts.css` y `tokens.css` antes que cualquier otro CSS.
- Sustituye la paleta verde actual por los tokens. Elimina el CSS muerto que dejes de usar.

### Paso 2 — Estructura común
- **Barra lateral** (`--side-*`): logo, navegación Consulta / Biblioteca (badge con nº de documentos o "N en cola" mientras haya cola activa), tarjeta resumen de la biblioteca con barra segmentada indexados/pendientes/error, y estado del sistema abajo (Ollama, Embeddings, Servidor con host:puerto en mono). Sin `position: sticky` ni `100vh`.
- **Barra superior**: migas ("Laboratorio / Consulta"), píldora "En tu equipo · sin conexión externa" y botón de refrescar estado.
- **Responsive**: fila flex con `flex-wrap`; la barra lateral pasa arriba en móvil; columnas de contenido con `flex: 999 1 560px` + `min-width: 0`; tablas anchas dentro de un contenedor con `overflow-x: auto`.

### Paso 3 — Biblioteca (antes Administración)
Reproduce `mockups/biblioteca.html`:
- Zona de arrastre compacta con botón "Seleccionar archivos" (input `type=file multiple`, `accept=".pdf,.txt,.md,.markdown"`) y estados normal / arrastrando / rechazo.
- **Cola de subida**: progreso total del lote (porcentaje grande, barra segmentada por fase, leyenda y tiempo restante) y una fila por archivo con chip de estado, barra y porcentaje; los archivos en fase de procesado muestran barra de 3 tramos (subida ✓ · fragmentos · índice). Acciones: cancelar, quitar, reintentar, pausar cola, limpiar completados. Interruptor "Indexar al terminar la subida". Toda la lógica en `spec/comportamiento.md` §2.
- **Documentos**: búsqueda por nombre, filtros por estado con contadores, tabla (Documento con chip de tipo, Tamaño, Estado, Fragmentos, Actualizado, acciones), fila resaltada para "en proceso" y "con error", acción "Reintentar" en errores, paginación si hay más de 20.
- **Estado del proceso**: pasos Documento → Fragmentos → Índice con el paso activo y su porcentaje, mini registro con las últimas 4–6 líneas de actividad (mono) y nota sobre CPU/Ollama.
- **Configuración**: lista de definición con colección, modelo de embeddings, dispositivo, fragmento/solape, generación; botón "Preparar modelo de búsqueda".

### Paso 4 — Consulta
Reproduce `mockups/consulta.html`:
- Cabecera con eyebrow, h1 y subtítulo; tres tarjetas de estadística (documentos, fragmentos, estado de Ollama).
- **Compositor**: textarea con halo de foco arcilla, sugerencias en chips (rellenan el textarea), selector de modelo (fuente mono), control segmentado de fragmentos (2/4/6/8, `aria-pressed`), interruptor "Solo buscar fragmentos" (`role="switch"`), botón "Preguntar" y ayuda `Ctrl + Enter`.
- **Respuesta**: estado, meta (modelo · nº fragmentos · tiempo), copiar y regenerar, eco de la pregunta, texto con citas `[n]` convertidas en chips que enlazan a su fuente, pie "Basada en N fragmentos de M documentos".
- **Fuentes consultadas**: tarjetas con número, nombre, tipo, ubicación (página/sección/líneas si existe), barra de relevancia + valor, extracto con términos resaltados y "Abrir en el documento" si el backend lo permite.
- Estados vacío, generando, error y "solo fragmentos" según spec §5.

### Paso 5 — Modales y avisos
Un único componente de diálogo reutilizable basado en `<dialog>` + `showModal()`, con variantes **éxito** y **error** (`role="alertdialog"`), más un aviso breve (toast) para acciones menores. Cuándo se usa cada uno, textos y comportamiento de foco: spec §4.

### Paso 6 — Backend mínimo (solo si falta)
Si el backend no da progreso o errores estructurados, añade lo mínimo, de forma aditiva:
- Subida **un archivo por petición** (permite progreso real por archivo con `XMLHttpRequest.upload.onprogress`; `fetch` no expone progreso de subida). Respuesta: `{ "job_id": "...", "document_id": "..." }`.
- `GET /api/jobs/{job_id}` → `{ "status": "queued|processing|done|error|cancelled", "stage": "upload|chunking|indexing", "progress": 0-100, "chunks": 214, "error": { "code": "NO_TEXT", "message": "...", "detail": "..." } }`.
- `GET /api/jobs?active=1` para recuperar la cola tras recargar la página.
- Opcional: `DELETE /api/jobs/{job_id}` para cancelar.
- Si ya hay SSE o WebSocket, úsalo en lugar de sondeo; si no, sondeo cada 1 s mientras haya trabajos activos y parada cuando no.
- Validación también en servidor: extensión, tamaño (20 MB) y nombre de archivo saneado (sin rutas, sin `..`).
Adapta nombres al estilo del proyecto y documenta el contrato en `NOTAS-REDISENO.md`.

## Criterios de aceptación

- [ ] Ninguna petición sale de `127.0.0.1` al cargar o usar la app (compruébalo en la pestaña Red).
- [ ] Todos los colores, radios, sombras y fuentes nuevos salen de `tokens.css`.
- [ ] Al soltar 6 archivos (uno > 20 MB, uno `.docx`), los 4 válidos entran en cola y los otros 2 aparecen como error inmediato con su motivo, sin llegar al servidor.
- [ ] Cada archivo muestra porcentaje real de subida; el total del lote avanza de forma monótona (nunca retrocede).
- [ ] Cancelar un archivo en subida aborta la petición; los demás continúan.
- [ ] Al terminar un lote sin errores aparece el modal de éxito; con algún error, el modal de error lista solo los fallidos con su motivo y acción, y resume cuántos sí entraron.
- [ ] Los modales se cierran con `Esc`, atrapan el foco y lo devuelven al control que los abrió.
- [ ] Recargar la página durante una indexación recupera la cola (si §6 se implementó).
- [ ] Consulta: `Ctrl + Enter` envía; las citas `[n]` enlazan a su fuente; "Solo buscar fragmentos" muestra fuentes sin respuesta.
- [ ] Biblioteca vacía y Consulta sin documentos muestran sus estados vacíos con llamada a la acción.
- [ ] A 390 px de ancho no hay scroll horizontal de página; la barra lateral queda arriba.
- [ ] Navegación completa con teclado y foco visible; lector de pantalla anuncia hitos de la cola (no cada porcentaje).
- [ ] Con `prefers-reduced-motion` no hay animaciones.
- [ ] Un nombre de archivo como `<img src=x onerror=alert(1)>.pdf` se muestra como texto literal en cola, tabla y modales.

## Entregables

1. Cambios en el código, en commits pequeños y descriptivos por paso.
2. `NOTAS-REDISENO.md` con decisiones, contrato de API (si lo añadiste) y cómo probar.
3. Si algo de la spec no es viable con el backend actual, **no lo simules con datos falsos**: deja el componente preparado, explícalo en las notas y propón el cambio mínimo.

## Fuera de alcance

Modo oscuro, cambios en el modelo de embeddings o en la fragmentación, autenticación, y cualquier rediseño del pipeline de ingesta.
