# Especificación de comportamiento — RAG Local

Complementa a `PROMPT.md`. Los textos entre comillas son **copia literal** para la interfaz.

## 1. Límites y formatos

| Regla | Valor |
|---|---|
| Formatos aceptados | `.pdf`, `.txt`, `.md`, `.markdown` |
| Tamaño máximo por archivo | 20 MB |
| Archivos por lote | 20 (el resto se rechaza con aviso) |
| Subidas simultáneas | 2 |
| Duplicado | mismo nombre **y** mismo tamaño que un documento ya indexado o en cola |

## 2. Cola de subida

### 2.1 Estados por archivo

```
           validación cliente
seleccionado ──────────────┬──────────────► rechazado (error inmediato, no se envía)
                           ▼
                       en_cola ──► subiendo(%) ──► subido ──┬──► fragmentando(%) ──► indexando(%) ──► indexado
                          │            │                    │
                          │            │                    └──► subido_sin_indexar   (si "Indexar al terminar" está apagado)
                          ▼            ▼
                      cancelado    cancelado / error            cualquier fase de servidor ──► error
```

| Estado | Chip (texto) | Colores (tokens) | Barra | Acción en la fila |
|---|---|---|---|---|
| en_cola | "En cola" | `--surface-muted` / `--ink-2` | vacía | Cancelar |
| subiendo | "Subiendo" | `--clay-tint` / `--clay-ink` | `--clay`, % real | Cancelar |
| fragmentando | "Fragmentando" | `--ochre-tint` / `--ochre-ink` | 3 tramos, tramo 2 en `--ochre` | Cancelar (si el backend lo permite) |
| indexando | "Indexando" | `--ochre-tint` / `--ochre-ink` | 3 tramos, tramo 3 en `--ochre` | — |
| indexado | "Indexado" | `--moss-tint` / `--moss-ink` | `--moss-bar` 100 % | Quitar de la cola |
| subido_sin_indexar | "Sin indexar" | `--surface-muted` / `--ink-2` | `--moss-bar` 100 % | "Indexar ahora" |
| error / rechazado | "Error" | `--brick-tint` / `--brick` | — (se muestra el motivo) | Reintentar (si es reintentable) o Quitar |
| cancelado | "Cancelado" | `--surface-muted` / `--ink-3` | — | Quitar |

La fila en proceso usa fondo `--ochre-row`; la fila con error, `--brick-row`.

Línea de meta bajo la barra:
- subiendo: "4,1 de 6,4 MB · 1,2 MB/s · ≈ 2 s"
- en_cola: "24 KB · esperando turno (2.º)"
- fragmentando/indexando: "12,8 MB · Paso 2 de 3: subida ✓ · fragmentos · índice"
- indexado: "4,2 MB · 214 fragmentos"

Formatos numéricos con `Intl.NumberFormat('es-ES')` (coma decimal, punto de miles). Tamaños en KB/MB con 1 decimal.

### 2.2 Progreso
- **Por archivo** (0–100): `0,4 × subida + 0,3 × fragmentos + 0,3 × índice`. Si el backend solo da un porcentaje de procesado, `0,4 × subida + 0,6 × procesado`.
- **Total del lote**: media ponderada por tamaño de los archivos no rechazados ni cancelados. Debe ser **monótono**: guarda el máximo mostrado y nunca pintes un valor menor.
- La barra total se segmenta por fase (indexado `--moss-bar`, en proceso `--ochre`, subiendo `--clay`) con leyenda.
- Velocidad y tiempo restante: media móvil de los últimos 3 s; muestra "Quedan ≈ 40 s" solo cuando haya al menos 2 s de muestra; si no, "Calculando…".

### 2.3 Acciones
- **Cancelar** archivo: `xhr.abort()` si está subiendo; si ya está en servidor, `DELETE /api/jobs/{id}` cuando exista; si no existe, deshabilita el botón en esa fase.
- **Reintentar**: vuelve a `en_cola` conservando el archivo en memoria. No se ofrece para `FILE_TOO_LARGE` ni `UNSUPPORTED_TYPE`.
- **Pausar** cola: no inicia nuevas subidas; las activas terminan. El botón pasa a "Reanudar".
- **Limpiar completados**: quita de la cola los indexados y cancelados.
- **Cancelar pendientes**: cancela todo lo que esté en cola o subiendo; pide confirmación solo si hay más de 3.
- Botón principal: "Subir e indexar (N)" en reposo; "Procesando 3 de 6…" con spinner y `aria-busy="true"` durante el lote. Con el interruptor apagado: "Subir (N)".
- Se puede seguir añadiendo archivos mientras la cola trabaja.

### 2.4 Zona de arrastre
- Normal: borde discontinuo `--line-dashed`, fondo `--surface-sunken`.
- Arrastrando: borde continuo `--clay`, fondo `--clay-tint`, texto "Suelta para añadir a la cola".
- Rechazo (ningún archivo válido): borde `--brick`, texto "Ningún archivo es compatible (PDF, TXT o Markdown)" durante 3 s.
- Texto: "Arrastra tus documentos aquí" / "Hasta 20 MB por archivo · 20 archivos por lote. Puedes seguir añadiendo mientras se procesa la cola."

### 2.5 Recuperación
Al cargar Biblioteca, `GET /api/jobs?active=1` (si existe) y reconstruye la cola. Las subidas que estaban en curso en el navegador no se pueden reanudar: muéstralas como "Interrumpido" con acción "Volver a añadir".

## 3. Tabla de documentos
- Filtros: "Todos · N", "Indexados · N", "En proceso · N", "Con error · N" (`aria-pressed`).
- Búsqueda por nombre con filtrado en cliente y `debounce` de 150 ms.
- Estado con punto de color + texto (nunca solo color). En proceso: mini barra + "Fragmentando 62 %".
- Documento recién indexado: chip "Nuevo" durante la sesión.
- Error en tabla: texto corto del motivo ("Necesita OCR") y botón "Reintentar".
- Pie: "Mostrando 6 de 12 · Los archivos sin cambios se omiten al actualizar. Los PDF escaneados necesitan OCR."
- Vacío: "Todavía no has añadido documentos. Empieza por la zona de arriba."

## 4. Modales y avisos

### 4.1 Cuándo
| Proceso | Resultado | Componente |
|---|---|---|
| Lote de subida/indexación | todo correcto | Modal **éxito** |
| Lote de subida/indexación | algún error (parcial o total) | Modal **error** (`role="alertdialog"`) |
| Actualizar índice | correcto | Modal éxito: "Índice actualizado" / "3 documentos actualizados · 9 sin cambios omitidos." |
| Actualizar índice | error | Modal error |
| Preparar modelo de búsqueda | correcto | Modal éxito: "Modelo de búsqueda listo" / "Los embeddings están descargados y cargados en CPU." |
| Preparar modelo de búsqueda | error | Modal error |
| Copiar respuesta, quitar de la cola, etc. | — | Toast 3 s ("Respuesta copiada") con `role="status"` |
| Error al consultar (Ollama caído, timeout) | — | **No modal**: error en línea dentro de la tarjeta de respuesta con "Reintentar" |

Si el usuario ha salido de Biblioteca cuando termina el lote, no abras el modal en otra pantalla: muestra un toast "Indexación terminada · Ver resultado" que abre el modal al pulsarlo.

### 4.2 Modal de éxito (ver `mockups/modal-exito.html`)
- Icono check en `--moss-tint`, título "Indexación completada", descripción "4 documentos nuevos ya forman parte de tu biblioteca. Puedes consultarlos desde ahora."
- Tres cifras: documentos, fragmentos nuevos, duración (m:ss).
- Lista de documentos con su nº de fragmentos (máx. 5 visibles + "y N más").
- Botones: "Seguir añadiendo" (secundario, cierra) y "Hacer una consulta" (primario, navega a Consulta). Foco inicial en el primario.

### 4.3 Modal de error (ver `mockups/modal-error.html`)
- Icono alerta en `--brick-tint`, título "N documentos no se han indexado" (singular: "1 documento no se ha indexado"), descripción "El resto del lote (M documentos) está en tu biblioteca. Revisa estos y vuelve a intentarlo." (si M = 0: "No se ha añadido ningún documento de este lote.").
- Una tarjeta por fallo: nombre, motivo (`--brick-ink`) y sugerencia (`--ink-2`), con acción contextual.
- `<details>` "Detalles técnicos" con `code` y `detail` del backend en mono, y botón "Copiar detalles".
- Botones: "Cerrar" y "Reintentar los fallidos" (primario en `--brick`, solo si hay alguno reintentable). Foco inicial en "Cerrar".

### 4.4 Mensajes por código de error
| Código | Motivo | Sugerencia | Acción | Reintentable |
|---|---|---|---|---|
| `FILE_TOO_LARGE` | "Supera el límite de 20 MB por archivo (23,1 MB)." | "Divídelo o comprímelo y añádelo de nuevo." | Quitar | No |
| `UNSUPPORTED_TYPE` | "Formato no compatible (.docx)." | "Conviértelo a PDF, TXT o Markdown." | Quitar | No |
| `NO_TEXT` | "No se ha podido extraer texto: parece un PDF escaneado." | "Actívale OCR y vuelve a procesarlo." | "Reintentar con OCR" (si existe OCR) / Quitar | Sí |
| `DUPLICATE` | "Ya está en la biblioteca sin cambios." | "No hace falta volver a indexarlo." | Quitar | No |
| `OLLAMA_UNAVAILABLE` | "Ollama no responde." | "Comprueba que Ollama está en marcha y reintenta." | Reintentar | Sí |
| `EMBEDDINGS_NOT_READY` | "El modelo de búsqueda no está preparado." | "Pulsa «Preparar modelo de búsqueda» en Configuración." | Reintentar | Sí |
| `NETWORK` | "Se perdió la conexión con el servidor local." | "Comprueba que la app sigue en marcha." | Reintentar | Sí |
| otro | "Error inesperado al procesar el archivo." | "Consulta los detalles técnicos." | Reintentar | Sí |

### 4.5 Comportamiento del diálogo
- `<dialog>` + `showModal()`; `aria-labelledby` (título) y `aria-describedby` (descripción).
- Atrapa el foco, `Esc` cierra, al cerrar devuelve el foco al control que lo abrió.
- Clic en el fondo cierra **solo** el de éxito.
- Fondo `::backdrop { background: var(--scrim); }`, radio `--radius-2xl`, sombra `--shadow-modal`, ancho `min(580px, 100vw - 32px)`.
- Entrada: opacidad 0→1 y escala .98→1 en `--dur-base` con `--ease-out`; sin animación con movimiento reducido.
- Bloquea el scroll del `body` mientras está abierto.

## 5. Consulta — estados
| Estado | Comportamiento |
|---|---|
| Biblioteca vacía | Banner "Tu biblioteca está esperando sus primeros documentos." con enlace "Añadir documentos →" a Biblioteca; botón "Preguntar" deshabilitado con ayuda "Añade documentos para poder consultar." |
| Sin texto | "Preguntar" deshabilitado. |
| Generando | Botón cambia a "Detener"; tarjeta de respuesta con estado "Generando…" y texto en streaming si el backend lo soporta (si no, esqueleto de 3 líneas); fuentes con esqueleto hasta que lleguen. `aria-live="polite"` anuncia "Respuesta lista" al terminar, no cada token. |
| Completada | Chip "Completada", meta "modelo · N fragmentos · 3,4 s". |
| Solo buscar fragmentos | No se llama a la generación; la tarjeta de respuesta se sustituye por "Mostrando los N fragmentos más relevantes." |
| Error | Mensaje en línea con el texto de §4.4 y botón "Reintentar"; la pregunta se conserva. |

- `Ctrl + Enter` (y `Cmd + Enter` en macOS) envía. Las sugerencias rellenan el textarea y ponen el foco al final.
- Citas: si la respuesta contiene `[1]`, `[2]`…, conviértelas en enlaces a `#src-n` (chip `--clay-tint`). Si el modelo no cita, no inventes citas: muestra las fuentes sin enlace.
- Relevancia: muestra el score normalizado 0–1 con 2 decimales y coma ("0,92"). Si el backend devuelve distancia, conviértela y documenta la fórmula. Color de la barra: ≥ 0,85 `--moss`, ≥ 0,75 `--moss-bar`, resto `--ochre`.
- Extracto: hasta 3 líneas con `line-clamp`, resaltando con `<mark>` los términos de la pregunta (mín. 4 letras, sin acentos/mayúsculas al comparar).

## 6. Accesibilidad
- Barras: `role="progressbar"` con `aria-valuenow/min/max` y `aria-label` con el nombre del archivo.
- Región `aria-live="polite"` para la cola que anuncia hitos: "actas-reuniones.pdf subido", "manual-usuario-v3.pdf indexado, 318 fragmentos", "escaneo-contrato.pdf con error". Nunca porcentajes sueltos.
- Botones solo con icono: `aria-label` descriptivo ("Cancelar actas-reuniones.pdf"). Iconos decorativos con `aria-hidden="true"`.
- Estado comunicado siempre con texto además del color.

## 7. Seguridad
- Nombres de archivo, extractos, respuestas del modelo y `detail` de errores: insértalos con `textContent` o creando nodos. El resaltado `<mark>` se construye partiendo el texto y creando nodos, nunca con `innerHTML`.
- Si la respuesta del modelo se renderiza como Markdown, pasa el HTML resultante por un saneador (lista blanca de etiquetas) y elimina enlaces `javascript:`.
- En servidor: valida extensión y tamaño, sanea el nombre (basename, sin `..` ni separadores) y no confíes en el `Content-Type` del cliente.
- Mantén el servidor ligado a `127.0.0.1`.

## 8. Puntos de ruptura
| Ancho | Cambios |
|---|---|
| ≥ 1200 px | Dos columnas (contenido + lateral 320–360 px). |
| 768–1199 px | La columna lateral (fuentes / estado / configuración) baja bajo el contenido. |
| < 768 px | Barra lateral arriba en bloque, tarjetas de estadística apiladas, tabla con scroll horizontal en su contenedor, botones de pie de cola a ancho completo. |
