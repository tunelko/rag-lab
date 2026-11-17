# RAG Local

[English](README.md) · **Español**

[![CI](https://github.com/tunelko/rag-lab/actions/workflows/ci.yml/badge.svg)](https://github.com/tunelko/rag-lab/actions/workflows/ci.yml)

Haz preguntas sobre tus propios documentos (PDF, texto, imágenes) desde una página
web en tu equipo. Los documentos se indexan en tu máquina y las respuestas citan su fuente.

## Requisitos

- Windows x64 o Linux x86_64
- [Python](https://www.python.org/downloads/) 3.13 de 64 bits
- [Git](https://git-scm.com/install/)
- [Ollama](https://ollama.com/download)

## Instalar

**1. Descargar el proyecto**

```text
git clone https://github.com/tunelko/rag-lab.git
cd rag-lab
```

**2. Descargar un modelo**

```text
ollama pull qwen3.5:9b-q4_K_M
```

**3. Abrir el asistente de instalación**

```powershell
# Windows
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\install\windows\Configurar.ps1
```

```bash
# Linux
bash install/linux/configurar.sh
```

**4. Seguir el asistente**

1. **Idioma:** elige Español o English.
2. **Bienvenida:** continúa.
3. **Entorno Python:** deja **Ollama GPU + embeddings CPU** y pulsa **Instalar dependencias**.
4. **Modelos Ollama:** pulsa **Actualizar modelos** y elige el modelo de texto. Para imágenes, activa multimodal y elige un modelo visual.
5. **Recuperación RAG:** deja los valores predeterminados.
6. **Revisar y guardar:** marca la confirmación y pulsa **Guardar configuración**.
7. **Listo:** pulsa **Iniciar web**.

## Usar

1. Abre **http://127.0.0.1:8765**.
2. En **Configuración**, pulsa **Preparar modelo de búsqueda**. Solo hace falta una vez.
3. En **Biblioteca**, pulsa **Subir e indexar** y espera a **Indexado**. Sigue el progreso en la franja del menú; púlsala para abrir **Estado del sistema** con los contadores de páginas y descripciones de imágenes.
4. En **Chat**, haz tu pregunta. **Contexto recuperado** muestra la fuente de la respuesta. Abre una fuente en el visor de documentos para leerla en una ventana modal o ampliarla a toda la aplicación; los PDF se abren en la página citada.

Opcional: [preparar un corpus de recetas](tools/recipes/README.md) con un adaptador separado; el RAG general y la biblioteca activa se mantienen.

**Datasets CTI / APTs:** en **Biblioteca**, pulsa **Añadir CTI · APTs**, selecciona la nueva colección y pulsa **Sincronizar CTI · ATT&CK 20.1**. La muestra semántica predeterminada es de 5.000 documentos; las relaciones documentadas completas se guardan aparte en SQLite. Cambia de colección en Biblioteca para volver a tus documentos actuales. Las descargas, originales, evidencias Markdown, exportaciones JSONL y licencias quedan en `datasets/cti/`; los índices e históricos de cada colección, en `data/collections/`. El chat interpreta las preguntas estructuradas con el modelo configurado y cita la evidencia real; las técnicas de un grupo no establecen la secuencia de un incidente.

**Grafo (piloto):** abre **Grafo**, selecciona documentos y pulsa **Preparar evidencias**. Elige proveedor y modelo para **Modelizar relaciones**; el envío remoto se selecciona expresamente. Las propuestas conservan pasajes literales y admiten revisión y reanudación. Esta primera entrega muestra una lista de relaciones: el mapa visual y su uso en las respuestas del chat siguen pendientes.

Antes de **Aceptar**, usa **Contrastar con la fuente** y justifica el resultado. **Verificar todas las colecciones** genera un informe de integridad y duplicados exactos, con un registro por documento; no certifica la veracidad de todo el contenido.

## Arrancar y parar

Ejecuta estas órdenes desde la carpeta del proyecto. El servidor sigue en marcha al cerrar la terminal.

| Acción | Windows | Linux |
|---|---|---|
| Arrancar | `.\install\windows\Start-Web.ps1 start` | `bash install/linux/web.sh start` |
| Parar | `.\install\windows\Start-Web.ps1 stop` | `bash install/linux/web.sh stop` |
| Reiniciar | `.\install\windows\Start-Web.ps1 restart` | `bash install/linux/web.sh restart` |
| Estado | `.\install\windows\Start-Web.ps1 status` | `bash install/linux/web.sh status` |

- **Otro puerto:** añade `-Port 8766` en Windows o `--port 8766` en Linux.
- **Otros equipos de tu red:** añade `-Host 192.168.1.20` en Windows o `--host 192.168.1.20` en Linux, con la dirección de este equipo. Quien pueda abrir la página comparte los mismos documentos; no hay cuentas.
- **Comprobar la instalación:** `.\install\windows\Configurar.ps1 -Check` o `bash install/linux/configurar.sh --check`.

## Tus datos

Todo queda dentro de la carpeta del proyecto y Git no sigue nada de ello.

| Ruta | Contenido |
|---|---|
| `data/` | Tus documentos, el índice y las preferencias de la web |
| `.env` | Configuración escrita por el asistente |
| `reports/` | Registros del servidor |

Las respuestas usan Ollama local salvo que elijas un proveedor en la nube en
**Configuración**; consulta [qué recibe un proveedor en la nube](docs/CLOUD_VISION.md).
La indexación es siempre local. Los límites de seguridad están en [SECURITY.md](SECURITY.md).
Las pruebas reales de seguridad y sus comandos están en [tests/README.md](tests/README.md).
