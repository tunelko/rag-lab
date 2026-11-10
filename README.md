# Laboratorio RAG local para Windows y RTX 3080

Entorno instalado en `D:\problemas_gpu\rag-lab`, aislado del Python global y de OMP.
Python 3.13 x64, PyTorch 2.14.1 con CUDA 13.0, Transformers 5.22.2,
bitsandbytes 0.50.2, Accelerate 1.15.0, LangChain 1.4.9 y ChromaDB 1.5.9.
Las dependencias directas están fijadas; `requirements-lock.txt` registra todo el entorno.

## Interfaz local: Chat, Biblioteca, Configuración y Estadísticas

Desde PowerShell, sin activar el entorno:

```powershell
& 'D:\problemas_gpu\rag-lab\Start-Web.ps1'
```

Mantén esa consola abierta. Ctrl+C cierra el servidor. Si el puerto está ocupado,
usa `Start-Web.ps1 -Port 8766` y cambia el puerto de las URLs.

- **Chat:** http://127.0.0.1:8765/ — conversación sin estadísticas ni ajustes.
  Cada respuesta tiene **Fuentes consultadas**, plegado de entrada. Pulsar una cita
  abre sus fuentes. El historial de hasta 30 turnos se conserva en esta pestaña.
- **Biblioteca:** http://127.0.0.1:8765/admin — subir e indexar documentos.
- **Configuración general:** http://127.0.0.1:8765/configuracion — selección automática/manual del modelo,
  2/4/6/8 fragmentos, búsqueda sin generación, procesamiento visual y sus límites.
  Un fragmento es un trozo del documento recuperado como evidencia para la pregunta.
  Los ajustes se guardan en `data/settings.json`, compartidos entre navegadores locales.
  **Guardar configuración** aplica los cambios a las consultas siguientes.
- **Estadísticas:** http://127.0.0.1:8765/estadisticas — biblioteca, servicios,
  tareas y tiempos/tokens de consultas recientes. Los datos de consultas se
  conservan en memoria del servidor, hasta 200 tareas; no representan un histórico
  permanente. Tokens/s proceden de las métricas nativas de Ollama, no de contar palabras.
  **Carga del equipo en tiempo real** añade lecturas cada 2 segundos de CPU, RAM,
  uso de GPU, VRAM, temperatura, potencia y consumo por proceso de RAG/Ollama.
  Las lecturas de GPU usan únicamente consultas de `nvidia-smi`; no cargan modelos
  ni cambian el modo de cómputo. Los valores no disponibles aparecen como «—».
- **Consola:** http://127.0.0.1:8765/?vista=consola — salida y actividad del servidor
  actualizada cada segundo. Incluye stdout, stderr, logs Python y avisos de procesado
  visual/caché. Permite pausar la visualización, buscar, filtrar por nivel, copiar,
  descargar y limpiar la vista. Es de lectura; no ejecuta comandos.
  Conserva las últimas 1000 líneas en memoria y limita la longitud de cada línea.
  Limpiar la vista no borra archivos ni el registro del servidor. Se consulta solo
  mientras está visible y se adapta también a móvil.
  El canal stderr no implica ERROR: los avisos y las barras de carga se clasifican
  por su contenido. Las horas con ≈ son horas de lectura de una salida recuperada,
  no la hora original demostrada del mensaje.

La captura integrada se inicia con el servidor. Para que una instancia anterior siga
indexando sin reiniciarla, `server_console_reader.py` lee los archivos fijos de salida
de ese proceso y sus fases reales mediante la API existente, y publica una instantánea
local para la misma sección. Finaliza cuando termina ese proceso o aparece el endpoint
integrado. Los mensajes de bibliotecas que se quedan en el búfer del proceso aparecen
al escribirse en el archivo; las fases se consultan cada segundo. Esta compatibilidad
no ejecuta inferencias ni modifica documentos, índices o preferencias.

### Primeros documentos

1. Revisa Configuración. Para procesar imágenes, activa el modo multimodal y elige
   un **modelo visual instalado** (por ejemplo `qwen3.5:9b-q4_K_M`, detectado localmente).
   Deja **Selección del modelo → Automática según la consulta** para que el chat
   elija entre el modelo de texto y el visual. También puedes elegir **Manual**.
2. En Biblioteca, añade PDF, TXT, Markdown, PNG/JPEG/WebP o un ZIP de exportación MinerU.
3. Pulsa **Subir e indexar**. Los embeddings usan CPU; su primera preparación puede
   descargar el modelo. Con el modo multimodal, las descripciones visuales se
   solicitan a Ollama durante esta acción y pueden utilizar la GPU.
4. En Chat, escribe la pregunta y pulsa **Enviar** o Ctrl+Enter.

El arranque y el guardado de ajustes no ejecutan inferencias ni descargan modelos.
En este equipo está activada la selección automática: BlackICE para texto y
`qwen3.5:9b-q4_K_M` para imágenes/indexación visual. La decisión usa la pregunta
y la evidencia recuperada. La instalación/capacidad de los modelos se ha comprobado
sin ejecutar inferencias reales. Puedes cambiar los candidatos en Configuración.
**Preparar modelo de búsqueda**, en Configuración, adelanta la descarga de embeddings CPU.
No se instala MinerU completo ni se descarga ningún LLM automáticamente.

### Adaptación multimodal

Referencia conceptual: [artículo de Joaquín Videla](https://www.joaquinvidela.com/blog/multimodal-rag-base-documentos-figuras)
y [skrvidz/multimodal-rag-base](https://github.com/skrvidz/multimodal-rag-base).
Implementación propia sobre el laboratorio existente; no se ha copiado su aplicación.

### Selección de modelo por consulta

El modo **Automático** usa reglas locales, sin ejecutar un clasificador ni otra
inferencia. Primero recupera las fuentes y después elige:

- Pregunta textual con evidencia textual: modelo para consultas de texto. Una
  imagen incidental en la misma página no obliga a adjuntarla ni cambiar de modelo.
- Pregunta sobre una imagen, gráfico, diagrama, ubicación o color, con imágenes
  recuperadas: modelo visual. También se usa si la evidencia principal recuperada
  es una descripción visual, para inspeccionar su original.
- Una continuación breve puede conservar la intención visual de la pregunta anterior.
- Sin imágenes originales relacionadas, con el modo multimodal desactivado o con
  el modelo visual no disponible, usa el modelo de texto y registra el motivo.
- Una petición explícita de usar solo texto evita el cambio automático a visión.

Es una heurística y puede equivocarse. El modo **Manual** fija el modelo de respuesta;
un cliente API que envíe `model` explícitamente también fuerza ese modelo. No hay
descargas ni intentos con modelos alternativos arbitrarios. **Estadísticas** muestra
el modelo realmente usado y el motivo de selección, incluso en los casos de reserva.
La elección afecta solo a esta consulta y no exige reindexar. En 10 GB de VRAM,
alternar dos modelos puede implicar que Ollama descargue uno de VRAM y cargue el otro.

### Lectura, recuperación y respuesta visual

- **PDF:** PyMuPDF extrae texto y tablas en CPU. Las páginas con imágenes, gráficos
  vectoriales o sin texto se conservan como vistas completas de página. No se
  afirma detectar y recortar automáticamente cada figura de un PDF.
- **Imágenes:** se normalizan y reducen a 1440 px como máximo por lado. Ollama
  genera una descripción/transcripción que entra en el índice textual.
- **MinerU ZIP:** un único `*_content_list_v2.json` o `*_content_list.json`
  con sus imágenes relativas. El importador preserva texto, tablas y figuras,
  convierte páginas de base 0 a páginas de base 1 y rechaza rutas fuera del ZIP.
  No admite ZIP genéricos, extracción Office directa ni exportaciones solo Markdown.
- **Recuperación:** embeddings textuales en ChromaDB; las descripciones permiten
  encontrar imágenes, pero no hay embeddings visuales ni búsqueda por imagen.
- **Respuesta:** cuando la selección elige visión, el modelo recibe hasta el límite configurado de imágenes
  de las páginas recuperadas. Un modelo de texto recibe los fragmentos/descripciones.
  La presencia de una miniatura en Fuentes no significa por sí sola que el modelo
  haya visto sus píxeles: el modo real aparece en Estadísticas.
- Descripciones automáticas/OCR pueden contener errores. Revisa la imagen y el original
  al trabajar con cifras, diagramas o tablas complejas. La calidad y el ajuste en VRAM
  no se han medido con inferencias reales durante esta adaptación.
- Las páginas/figuras y sus descripciones se guardan en `data/visual`; la caché evita
  repetir descripciones idénticas con el mismo modelo/parser. El manifiesto del índice
  guarda referencias a figuras y la política de extracción.
- Límite predeterminado para nuevas configuraciones: 500 vistas/figuras por documento.
  Los ajustes guardados conservan su valor hasta cambiarlo en Configuración. El recuento
  se comprueba antes de solicitar descripciones a Ollama; si se supera, aparece el total
  necesario y no se confirma una indexación parcial. Las imágenes se procesan una a una:
  subir este límite amplía el trabajo total, no reserva VRAM para todas simultáneamente.
  Cambiar solo el límite conserva los documentos ya indexados, incluidos los manifiestos
  anteriores que almacenaban ese límite como parte de la política de extracción.
  Cambiar el modelo visual o activar/desactivar el modo requiere **Actualizar índice**.
  El CLI textual no sustituye un índice multimodal existente: usa la web para actualizarlo.

Los documentos originales quedan en `data/documents`. Se aceptan 20 archivos por
lote, 20 MB por archivo, dos subidas simultáneas y procesamiento secuencial.
Los ZIP admiten hasta 2000 entradas y 100 MB descomprimidos, sin enlaces simbólicos.
No se sobrescriben originales: los archivos cambiados se guardan con otro nombre.

El servidor escucha en 127.0.0.1, valida Host/origen y exige token CSRF para escrituras.
Es una aplicación personal con secciones, sin cuentas o roles autenticados. No se
publica en la LAN ni Internet. Documentos, imágenes, embeddings y consultas se
procesan en local; la primera descarga de dependencias/embeddings sí necesita Internet.
No se utiliza Claude ni Qdrant, Docker o un servicio externo de análisis documental.

La cola conserva progreso real, pausa, cancelación, reintentos y recuperación al
recargar. La cancelación visual es cooperativa: puede esperar a que Ollama entregue
el siguiente bloque. La escritura de lotes del índice no se interrumpe a mitad.
**Cancelar procesamiento**, en Biblioteca, detiene subidas, cancela los trabajos
en espera y solicita detener la extracción visual activa. Conserva originales y caché;
si ya se está escribiendo el índice, esa escritura termina antes de quedar inactivo.
Después de reiniciar el servidor, las tareas antiguas que ya no existen se muestran
como canceladas; sus archivos siguen disponibles para **Actualizar índice**.
El chat transmite al modelo los últimos tres pares de mensajes, acotados; recuperar
evidencia sigue basándose en la pregunta actual. No implementa las herramientas
iterativas del agente del repositorio de referencia.
Las llamadas de este RAG desactivan el thinking de Ollama para priorizar respuestas
visibles dentro del límite de salida. Las consultas con imágenes piden 8192 tokens
de contexto; se puede ajustar con `RAG_OLLAMA_VISION_CONTEXT` en `.env`. Son opciones
de cada petición del laboratorio, sin cambiar la configuración global de Ollama/OMP.
El historial transmitido se limita además a 6000 caracteres en total.

El informe `reports/redesign-qa.json` y sus capturas usan modelos y métricas
**simulados para probar la aplicación**, no son benchmarks de la RTX 3080.
La UI se comprueba en Edge a 1440 px y 390 px, sin peticiones externas del navegador.
Se mantienen los tokens y tipografías locales del paquete de rediseño.

## Entrar al entorno

Desde PowerShell:

```powershell
Set-Location 'D:\problemas_gpu\rag-lab'
. .\Enter-Lab.ps1
python verify_env.py
```

La verificación predeterminada comprueba la disponibilidad de CUDA mediante NVML,
sin inicializar un contexto CUDA ni reservar tensores GPU. Importa las librerías y
prueba tensores CPU en otro proceso que tiene la GPU oculta.
No descarga ni ejecuta ningún LLM o modelo de embeddings.

Si la política de tu consola impide activar entornos, puedes usar directamente:

```powershell
.\.venv\Scripts\python.exe verify_env.py
```

Cuando hayas dejado libre la GPU, esta comprobación OPCIONAL ejecuta una pequeña
operación CUDA y una capa bitsandbytes NF4. No la ejecutamos durante la instalación:

```powershell
python verify_env.py --cuda-smoke --write-report
```

## Arquitectura

```mermaid
flowchart LR
    A[PDF / TXT / Markdown] --> B[Chunking 500 caracteres / solapamiento 80]
    B --> C[Embeddings E5 multilingües en CPU]
    C --> D[(ChromaDB en disco)]
    Q[Pregunta] --> E[Mismo E5 en CPU]
    E --> F[Similitud de coseno / top K]
    D --> F
    F --> P[Contexto con referencias]
    Q --> P
    P --> L[Ollama o HF NF4]
    L --> R[Respuesta y fuentes]
```

La ingesta y la recuperación usan CPU de manera predeterminada. La generación
solamente se ejecuta con `ask` sin `--preview`. No se inicia ningún servidor en
segundo plano ni se cambia la configuración de Ollama o de OMP.

### 1. Documentos e ingesta

Coloca PDF con texto, TXT UTF-8 y Markdown en `data\documents`, incluidos subdirectorios.
La primera ingesta necesita descargar explícitamente el modelo de embeddings:

```powershell
python rag.py ingest --download-embeddings
```

Se utiliza `intfloat/multilingual-e5-small` (384 dimensiones), con vectores normalizados
y los prefijos `passage: ` para documentos y `query: ` para preguntas, también en español.
Las cachés se guardan dentro de `.cache` en este proyecto de D:.
El modelo de embeddings se carga en CPU, con lotes de 16.
No se ha descargado durante la preparación del laboratorio.

Después, para actualizar el índice sin permitir nuevas descargas:

```powershell
python rag.py ingest
```

La ingesta compara SHA-256 y omite archivos sin cambios. Actualiza fragmentos de
archivos modificados sin duplicarlos y guarda documento, página y posición inicial.
Si cambias el modelo de embeddings o el chunking, usa otro `RAG_COLLECTION` en `.env`.
Los archivos retirados del directorio no se eliminan automáticamente del índice:
para reconstruir un corpus limpio, selecciona una colección nueva y reingesta.
Ejecuta una sola ingesta por colección a la vez.

El CLI textual no realiza OCR de PDF escaneados. Usa la ingesta multimodal de la web
para describir sus páginas con el modelo visual local.
Los fragmentos son de 500 CARACTERES, no tokens. E5 limita su entrada a 512 tokens:
para textos con símbolos o caracteres muy densos, reduce el tamaño de fragmento.

### 2. Recuperación sin LLM

```powershell
python rag.py search '¿Qué especifica el documento sobre el procedimiento?' -k 4
python rag.py ask '¿Qué especifica el documento sobre el procedimiento?' --preview
```

`search` muestra textos, fuentes y distancia de coseno (menor significa más cercano).
`ask --preview` muestra el prompt aumentado y las referencias, sin llamar a un LLM.
El modelo de embeddings debe estar ya descargado en la caché.

### 3. Generación con Ollama

Esta fase sí envía una solicitud de inferencia a Ollama y usa la GPU según su configuración.
El CLI apunta al BlackICE configurado en `.env`. Cámbialo allí si prefieres otro
modelo instalado. La selección automática según la pregunta se aplica a la web;
este comando CLI conserva su modelo fijo y no descarga otro automáticamente.

```powershell
python rag.py ask '¿Qué especifica el documento sobre el procedimiento?'
```

La salida incluye referencias `[1]`, `[2]`, las fuentes y tokens/s cuando Ollama los devuelve.
El cliente no fija `num_ctx`, no descarga modelos ni detiene procesos existentes.
Los valores de `RAG_CONTEXT_CHARS`, `RAG_TOP_K` y `RAG_MAX_NEW_TOKENS` limitan el prompt
y la respuesta. El número de caracteres no es una garantía del número de tokens del LLM.

### Alternativa: Transformers y bitsandbytes NF4

Este backend está preparado pero no se ha validado ejecutando un LLM en la GPU.
Primero deja VRAM suficiente y selecciona un modelo instruct compatible con Transformers,
preferiblemente 4B-8B. Un modelo GGUF de Ollama no se carga mediante bitsandbytes.

```powershell
# Para pesos ya presentes en disco o en la caché:
python rag.py ask 'Tu pregunta' --backend hf4bit --model 'D:\ruta\al\modelo'

# Solo para permitir explícitamente la descarga de un ID de Hugging Face:
python rag.py ask 'Tu pregunta' --backend hf4bit --model 'organizacion/modelo' --download-model
```

La carga usa NF4, doble cuantización, cálculo FP16 y pesos safetensors, sin código remoto.
Rechaza VRAM insuficiente y la descarga a CPU/disco del modelo. La comprobación de memoria
es preventiva: los pesos, las activaciones y el KV cache pueden seguir causando OOM según
el modelo y el contexto. No se carga ningún modelo HF durante la instalación.
Los 10 GB de VRAM son físicos; la memoria compartida de Windows no equivale a VRAM.

## Pruebas y reinstalación

```powershell
python -m unittest -v test_rag
python -m pip check
```

Las pruebas usan vectores falsos deterministas y no miden calidad semántica. Comprueban
lectura TXT/PDF, persistencia entre procesos, recuperación, deduplicación, actualización,
validación de configuración y referencias. Crean una base diminuta en `data\test-chroma`.
El informe de detección y librerías queda en `reports\verification.json`.

Pruebas de la web y del streaming, sin modelos reales:

```powershell
python -m unittest -v test_rag test_web test_ollama_stream test_redesign
# Requiere requirements-dev.txt y Edge; inicia y cierra su servidor de pruebas aislado.
python qa_redesign.py
# Solo lectura contra tu servidor ya iniciado:
python qa_web.py --url http://127.0.0.1:8765
```

Para recrear las mismas versiones con Python 3.13 x64:

```powershell
.\Setup-Lab.ps1 -UseLock
```

No se instala CUDA Toolkit global, no se actualizan controladores y no se modifica
el Python global. Los wheels de PyTorch aportan el runtime CUDA necesario.

## Límites y próximos pasos

- Las fuentes permiten contrastar la respuesta; RAG no garantiza que el LLM sea correcto.
- Las instrucciones del prompt no eliminan por completo el riesgo de prompt injection en documentos.
- Falta validar recuperación y respuestas con tu propio corpus y preguntas con respuesta conocida.
- Un umbral de distancia, búsqueda híbrida y reranking deben calibrarse con esas preguntas,
  no añadirse con valores arbitrarios. El siguiente paso útil es una pequeña evaluación del corpus.
- Todo es local por defecto. Si cambias la URL de Ollama por un servidor remoto, le enviarás el contexto recuperado.

Documentación usada: [PyTorch](https://pytorch.org/get-started/previous-versions/),
[bitsandbytes](https://huggingface.co/docs/bitsandbytes/installation),
[LangChain + Chroma](https://docs.langchain.com/oss/python/integrations/vectorstores/chroma)
y [E5 multilingüe](https://huggingface.co/intfloat/multilingual-e5-small).
