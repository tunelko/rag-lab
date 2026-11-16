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
4. En **Chat**, haz tu pregunta. **Contexto recuperado** muestra la fuente de la respuesta.

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
