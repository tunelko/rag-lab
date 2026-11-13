# RAG Local

[English](README.md) · **Español**

[![CI](https://github.com/tunelko/rag-lab/actions/workflows/ci.yml/badge.svg)](https://github.com/tunelko/rag-lab/actions/workflows/ci.yml)

Asistente documental local y multimodal para Windows y Linux. Consulta PDF, textos
e imágenes mediante un chat con fuentes verificables y una interfaz web con
estadísticas y consola. Incluye un asistente de terminal inspirado en OMP.

## Instalar

**Requisitos**

- Windows x64 o Linux x86_64
- [Python](https://www.python.org/downloads/) 3.13 de 64 bits
- [Git](https://git-scm.com/install/)
- [Ollama](https://ollama.com/download)

**1. Descargar el proyecto**

```text
git clone https://github.com/tunelko/rag-lab.git
cd rag-lab
```

**2. Descargar un modelo**

```text
ollama pull qwen3.5:9b-q4_K_M
```

Este modelo admite texto e imágenes y ocupa varios GB. Sirve cualquier modelo local de Ollama.

**3. Abrir el asistente de instalación**

```powershell
# Windows
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Configurar.ps1
```

```bash
# Linux
bash configurar.sh
```

**4. Seguir el asistente**

1. **Idioma:** elige Español o English.
2. **Bienvenida:** continúa.
3. **Entorno Python:** deja **Ollama GPU + embeddings CPU** y pulsa **Instalar dependencias**.
4. **Modelos Ollama:** pulsa **Actualizar modelos**, elige el modelo de texto y, para imágenes, activa multimodal con un modelo visual.
5. **Recuperación RAG:** deja los valores predeterminados.
6. **Revisar y guardar:** marca la confirmación y pulsa **Guardar configuración**.
7. **Listo:** pulsa **Iniciar web** y abre **http://127.0.0.1:8765**.

Para volver a arrancar más adelante: `.\Configurar.ps1 -Start` en Windows o
`bash configurar.sh --start` en Linux. Más detalle en la [guía de instalación](docs/INSTALACION.md).

Para controlar el servidor en segundo plano, desde el directorio del proyecto:

```powershell
.\Start-Web.ps1 start
.\Start-Web.ps1 status
.\Start-Web.ps1 restart
.\Start-Web.ps1 stop
```

En Linux: `bash web.sh start|status|restart|stop` (elige una acción).
Ambos lanzadores admiten también `--start`, `--stop`, `--restart`, `--status` y `--help`.
En Windows también funcionan `-Restart` y los demás switches; `-h` muestra la ayuda.
Usa el puerto de `.env`; puedes cambiarlo con `-Port 8766` en Windows o
`--port 8766` en Linux. Registros en `reports/rag-server-runtime*.log`.
Si hay tareas activas, stop/restart pide terminarlas o cancelarlas primero.
Estos comandos gestionan solo el servidor RAG y conservan los documentos.

## Primera consulta

En **Configuración**, pulsa **Preparar modelo de búsqueda** para descargar los
embeddings CPU. Añade un TXT pequeño en **Biblioteca → Subir e indexar** y espera
a **Indexado**. Pregunta en **Chat** y abre **Fuentes consultadas** para contrastar
el original. Prueba después un PDF corto antes de indexar lotes grandes.

## RAG multimodal

El chat ocupa todo el espacio de trabajo. Las citas abren un panel lateral de
fuentes, que se cierra con Escape. El proveedor/modelo, el modo automático/manual,
el número de fragmentos y «Solo recuperación» se establecen en Configuración
(engranaje superior), se guardan automáticamente y se aplican al chat. El botón
**+** adjunta imágenes. El icono de estado del sistema abre el detalle de conexión
y hardware. El selector con bandera está arriba a la derecha; fuentes tipográficas
y traducciones se sirven localmente.

El proveedor de respuesta puede seguir siendo **Ollama local** o cambiarse a
Scaleway, OVHcloud, xAI/Grok o **Groq**. Un proveedor externo recibe la pregunta,
el historial y los fragmentos recuperados. **Modelo visual para respuestas** puede
enviar las imágenes necesarias para esa respuesta. **Indexación local** usa siempre
Ollama para describir figuras y CPU para extracción/embeddings; nunca llama a un
proveedor externo. Su modelo visual se configura por separado. Cambiar proveedor
o modelo de respuestas no requiere reindexar. Consulta [respuestas externas e indexación local](docs/CLOUD_VISION.md).
Las claves API se introducen desde
Configuración en el propio equipo servidor (por loopback o por su dirección de escucha) y nunca vuelven al navegador:
Windows las cifra con DPAPI ligada al usuario y Linux con AES-GCM y una contraseña
maestra conservada solo en memoria.
La lista desplegable **Modelo externo** carga el catálogo automáticamente al guardar
una credencial o abrir un proveedor configurado. **Actualizar modelos** vuelve a
consultar `/v1/models` desde el servidor. Elige un modelo de la lista: la selección
se guarda automáticamente y se conserva al recargar la página. En Scaleway se guarda la
**Secret Key**, usada como Bearer por Generative APIs; la Access Key no interviene
en la inferencia. Su portador IAM necesita `GenerativeApisModelAccess` (mínimo
privilegio), `GenerativeApisFullAccess` o `AllProductsFullAccess` para el proyecto
u organización correspondientes.

Para Groq, selecciona **Groq** en Configuración, guarda una API key de la
[consola de Groq](https://console.groq.com/keys) y elige un modelo de la lista
consultada en directo. Se excluyen modelos de audio, clasificación y sistemas
Compound. El streaming admite los contadores nativos de Groq. Las tablas de toda
la biblioteca usan modo JSON con la validación por documento de la aplicación.
Las tarifas de Groq no están integradas; se muestran como no disponibles.

En Scaleway, el modelo seleccionado muestra también las tarifas de entrada/salida
y de entrada en caché cuando existen. Se consultan por API pública de Product
Catalog cada vez que se actualiza el catálogo: EUR por millón de tokens, o por
minuto para audio. El panel indica la hora y fuente de la consulta, excluye los
precios por lotes y no aplica impuestos, tramo gratuito ni descuentos de cuenta.
Una tarifa ausente se muestra como no disponible, nunca como gratuita. Los otros
proveedores aún no tienen integrada la consulta de precios por API.

En **Biblioteca**, organiza los documentos en **categorías y subcategorías** desde
el árbol lateral. El botón **+** crea una categoría; el menú de cada una permite
añadir subcategorías, renombrar o eliminar categorías vacías.
Puedes mover una selección de documentos o elegir el destino antes de subirlos.
En el chat, **Fuentes** permite consultar toda la biblioteca, una categoría con sus
subcategorías, una subcategoría o los documentos sin categoría. Esta organización se guarda
en `data/` y conserva PDF, vectores y descripciones ya procesadas.
**Vaciar biblioteca** permite empezar de cero: tras escribir una palabra de
confirmación, mueve documentos, índice y categorías a una carpeta con fecha en
`data/library-backups/` en lugar de borrarlos.
[Organización de la biblioteca](docs/LIBRARY.md).

- **Ingesta:** extrae texto y tablas de PDF y renderiza páginas visuales o escaneadas.
  El modelo visual local de Ollama describe las imágenes; texto y descripciones entran en el almacén vectorial SQLite local.
- **Recuperación:** busca en texto y descripciones con embeddings multilingües.
- **Ordenación:** las preguntas enfocadas usan un canal de texto nativo, ámbito
  documental automático y un cross-encoder local opcional. Consulta el [pipeline de recuperación](docs/RETRIEVAL.md#focused-retrieval-pipeline).
- **Respuesta:** usa Ollama local o un proveedor externo elegido expresamente. La
  consulta visual puede adjuntar imágenes al modelo visual elegido. Las respuestas incluyen citas.
- **Seguimiento:** Biblioteca muestra trabajos, Estadísticas aporta gráficas y tiempos,
  y Consola recoge la salida del servidor. El chat muestra Markdown y código.

Estadísticas conserva [siete días de histórico local](docs/STATISTICS.md), con vistas
por día/semana y registros de consultas e indexado. La [auditoría visual sin modelos](docs/VISUAL_AUDIT.md)
permite evaluar páginas y etiquetas manuales antes de cambiar la ingesta multimodal.
Una [evaluación local experimental de recuperación](docs/EVALUATION.md) compara ranking denso exacto,
FTS5 e híbrido mediante Recall@K, MRR, nDCG y P50/P95 sobre preguntas etiquetadas.

El [Laboratorio](docs/LABORATORY.md) valida preguntas JSONL locales y mide la
recuperación concreta del chat sobre una copia temporal del índice. Muestra la
evidencia literal y el contexto exacto por pregunta, permite cancelar y recupera
los resultados al recargar. No genera respuestas ni cambia los ajustes del chat.

Admite PDF, TXT, Markdown, PNG/JPEG/WebP y exportaciones MinerU compatibles. Las
descripciones se reutilizan desde caché. La ingesta visual puede tardar segundos
por página. La cola es secuencial: las preguntas esperan durante la indexación.
Contrasta los detalles delicados con los originales.

Los recuentos y listados consultan el registro real del índice. Las listas generales
como «Dame la lista de cpus admitidas» y las preguntas sobre todos los documentos
recuperan evidencia de cada archivo con un presupuesto de
contexto compartido; 2/4/6/8 fragmentos se aplica a consultas concretas. [Detalles](docs/RETRIEVAL.md).

## Organización y entornos

```text
requirements/     Perfiles de aplicación, CPU/CUDA, asistente y desarrollo
tests/unit/       Regresión Python sin modelos reales
tests/browser/    QA de navegador aislada y comprobaciones de solo lectura
tests/frontend/   Pruebas JavaScript
dist/             Interfaz web, fuentes y librerías locales
docs/             Guías de instalación, referencia y desarrollo
docs/archive/     Material histórico de diseño
benchmarks/       Diagnósticos sintéticos y evaluaciones privadas
data/             Datos locales privados, fuera de Git
```

El perfil recomendado es `requirements/ollama.txt`; HF/NF4 usa `requirements/cuda.txt`.
PyTorch tiene archivos CPU/CUDA separados; el asistente gestiona el orden. Consulta
[dependencias](requirements/README.md) y [pruebas](tests/README.md).

```text
python -m unittest discover -s tests -t . -v
node --test tests/frontend/*.test.mjs
python -m tests.browser.qa_redesign
```

[CI](https://github.com/tunelko/rag-lab/actions/workflows/ci.yml) ejecuta pruebas CPU
en Windows/Linux, con QA de navegador en ambos, modelos falsos y corpus aislado.
Ollama gestiona la GPU independientemente de los embeddings CPU.

El servidor escucha en loopback por defecto. `-Host`/`--host` permite el acceso
desde una LAN de confianza; consulta la [instalación](docs/INSTALACION.md#uso-en-la-red-local).
Documentos, modelos, índices y ajustes privados
quedan fuera de Git. Las descargas iniciales necesitan Internet. Más:
[instalación](docs/INSTALACION.md), [referencia completa](docs/REFERENCE.es.md),
[diagnóstico](docs/DESARROLLO.md). Licencia propia pendiente; se conservan las de terceros.
Avisos de dependencias y límites del despliegue local: [estado de seguridad](SECURITY.md).
Arquitectura comprobada, fallos corregidos y límites pendientes: [revisión técnica](docs/AUDIT.md).

Interfaz en español e inglés, detección del navegador, selector de idioma
persistente y conmutador de apariencia clara/oscura. Las nuevas respuestas usan el
idioma elegido mediante Ollama local. Consulta
[idiomas](docs/INTERNATIONALIZATION.md) y el [plan de escalabilidad](docs/SCALING.md).
