# RAG Local

[English](README.md) · **Español**

[![CI](https://github.com/tunelko/rag-lab/actions/workflows/ci.yml/badge.svg)](https://github.com/tunelko/rag-lab/actions/workflows/ci.yml)

Asistente documental local y multimodal para Windows y Linux. Consulta PDF, textos
e imágenes mediante un chat con fuentes verificables y una interfaz web con
estadísticas y consola. Incluye un asistente de terminal inspirado en OMP.

## Instalar

**Requisitos:** Python **3.13 x64**, Git y Ollama; Windows x64 o Linux x86_64.
Instala [Python](https://www.python.org/downloads/windows/),
[Git](https://git-scm.com/install/) y [Ollama](https://ollama.com/download).
Para Python en Linux, consulta la [guía completa](docs/INSTALACION.md).

**1. Descargar el proyecto:**

```text
git clone https://github.com/tunelko/rag-lab.git
cd rag-lab
```

**2. Comprobar Ollama y descargar un modelo:**

```text
ollama list
ollama pull qwen3.5:9b-q4_K_M
```

Este ejemplo descarga varios GB y admite texto y visión. Elige un modelo adecuado
para tu equipo. Si `ollama list` no conecta, abre Ollama o ejecuta `ollama serve`
en otra terminal.

**3. Abrir el asistente desde la carpeta del proyecto:**

```powershell
# Windows — PowerShell, política aplicada solo a este proceso
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Configurar.ps1
```

```bash
# Linux — necesita Python 3.13 con soporte venv
bash configurar.sh
```

**4. Seguir estas pantallas:**

1. **Entorno Python:** elige **Ollama GPU + embeddings CPU**, pulsa **Instalar dependencias** y espera a que termine.
2. **Modelos Ollama:** actualiza la lista, elige texto y activa multimodal con un modelo visual. El mismo modelo compatible puede cubrir ambos usos.
3. **Recuperación RAG:** empieza por 4 fragmentos y los valores predeterminados.
4. **Revisar y guardar:** revisa, marca la confirmación y guarda.
5. **Listo:** pulsa **Iniciar web** y abre **http://127.0.0.1:8765**.

El asistente instala paquetes en `.venv`, mantiene su propio entorno en `.cache`,
conserva los datos y crea copias de configuración. Mantén abierta la consola del
servidor; **Ctrl+C** lo cierra. Para volver a arrancar: `Configurar.ps1 -Start` en
Windows o `bash configurar.sh --start` en Linux. [Guía detallada](docs/INSTALACION.md).

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

- **Ingesta:** extrae texto y tablas de PDF y renderiza páginas visuales o escaneadas.
  Un modelo visual local describe las imágenes; texto y descripciones entran en ChromaDB.
- **Recuperación:** busca en texto y descripciones con embeddings multilingües.
- **Respuesta:** elige texto o visión según la pregunta y las fuentes; puede adjuntar
  imágenes originales al modelo visual y devuelve citas verificables.
- **Seguimiento:** Biblioteca muestra trabajos, Estadísticas aporta gráficas y tiempos,
  y Consola recoge la salida del servidor. El chat muestra Markdown y código.

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
benchmarks/       Diagnósticos CPU con datos sintéticos
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
en Windows/Linux y QA de navegador en Linux con modelos falsos y corpus aislado.
Ollama gestiona la GPU independientemente de los embeddings CPU.

El servidor escucha en loopback. Documentos, modelos, índices y ajustes privados
quedan fuera de Git. Las descargas iniciales necesitan Internet. Más:
[instalación](docs/INSTALACION.md), [referencia completa](docs/REFERENCE.es.md),
[diagnóstico](docs/DESARROLLO.md). Licencia propia pendiente; se conservan las de terceros.
