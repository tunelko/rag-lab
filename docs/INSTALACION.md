# Instalación y configuración con asistente

[English installation guide](../README.md) · [Inicio rápido en español](../README.es.md)

El mismo asistente de terminal funciona en Windows y Linux, con estética inspirada
en OMP: pasos a la izquierda, configuración en el centro, ayuda a la derecha y
registro de operaciones debajo. Usa Textual; no necesita OMP, Node ni un navegador.
En terminales estrechas oculta primero la ayuda y después la barra de pasos.

## 1. Requisitos

- Windows x64 o Linux x86_64.
- Python **3.13**, con `venv` y `pip`. En Linux el soporte de entornos virtuales
  puede requerir el paquete `python3.13-venv` de tu distribución.
- Ollama instalado para generar respuestas y describir imágenes.
- Internet para instalar paquetes y descargar los modelos que elijas por separado.
- Espacio en disco para dependencias, modelos y documentos. El perfil CUDA ocupa
  bastante más que el perfil CPU usado por Ollama.

El asistente no instala Python, Ollama ni controladores del sistema. Si falta
alguno, muestra el error y conserva los archivos existentes. Sus descargas Python
se limitan al proyecto; no usa `sudo` ni modifica el Python global.

## 2. Abrir el asistente

Abre una terminal en la carpeta del repositorio.

**Windows, PowerShell:**

```powershell
.\Configurar.ps1
```

Si la política impide ejecutar el script, esta alternativa afecta solo al proceso
que abre el asistente:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Configurar.ps1
```

Para indicar un intérprete concreto:

```powershell
.\Configurar.ps1 -Python 'C:\ruta\a\Python313\python.exe'
```

**Linux:**

```bash
bash configurar.sh
```

También puedes especificar el intérprete:

```bash
RAG_SETUP_PYTHON=/ruta/a/python3.13 bash configurar.sh
```

El primer arranque prepara **`.cache/wizard-venv`** e instala la versión fijada de
Textual. Los siguientes reutilizan ese entorno. La aplicación RAG usa **`.venv`**;
son entornos separados. Antes de la instalación del RAG puedes recorrer el asistente.

Teclado: **Tab / Shift+Tab**, flechas y Enter para los controles;
**Ctrl+→ / Ctrl+←** para avanzar/retroceder y **Ctrl+Q** para salir.
Salir sin guardar conserva tu configuración. Las operaciones iniciadas con
«Instalar dependencias» sí instalan paquetes aunque todavía no hayas guardado ajustes.

## 3. Comprobar el equipo

La bienvenida muestra sistema, Python, entorno existente, conexión a Ollama,
modelos locales y tareas RAG activas. «Comprobar equipo» refresca los metadatos.
No ejecuta inferencias ni inicializa CUDA.

Puedes hacer esta comprobación sin instalar Textual ni abrir la interfaz:

```powershell
.\Configurar.ps1 -Check
```

```bash
bash configurar.sh --check
```

La salida JSON contiene estado y nombres de modelos, sin leer ni mostrar tokens
de Hugging Face u otras credenciales.

## 4. Elegir e instalar el perfil

| Perfil | Qué instala | Para qué sirve |
|---|---|---|
| Ollama GPU + embeddings CPU | PyTorch CPU y dependencias de la web/RAG | Recomendado para generar con Ollama, que gestiona la GPU por su cuenta. |
| PyTorch CUDA 13.0 + HF NF4 | PyTorch CUDA, Transformers, bitsandbytes y dependencias RAG | Backend avanzado de generación HF en una GPU NVIDIA compatible con esa compilación. |

El perfil CUDA requiere un controlador NVIDIA compatible; el asistente no lo
actualiza. La selección del perfil no garantiza que cualquier modelo quepa en VRAM.
Los modelos se eligen y descargan por separado. Los GGUF de Ollama no se cargan
con bitsandbytes.

En una instalación existente se conserva como selección inicial el perfil guardado
o la compilación CUDA detectada en `.venv`. Revisa los comandos de la pantalla:
«Instalar dependencias» los ejecuta y transmite la salida al panel inferior.
No ejecuta un LLM, no descarga embeddings y no cambia la configuración global de Ollama.

Si el RAG está abierto, la instalación se bloquea para no actualizar paquetes de un
servidor en uso. Puedes seguir revisando el resto de pantallas y volver después.
Las versiones están fijadas. El lock histórico completo es de Windows; en Linux
el asistente usa las dependencias directas y la resolución correspondiente a ese SO.

## 5. Configurar Ollama y modelos

1. Abre Ollama o ejecuta `ollama serve` si todavía no hay un servidor escuchando.
2. Mantén `http://127.0.0.1:11434`, salvo que tu instancia local use otro puerto.
3. Pulsa «Actualizar modelos».
4. Elige el modelo de texto de la lista o escribe su nombre exacto.
5. Para PDF con figuras e imágenes, activa multimodal y elige un modelo que declare
   capacidad de visión en Ollama.
6. Selecciona modo automático o manual de respuesta.

Las listas consultan `/api/tags` y `/api/show`; no cargan modelos. Los modelos
declarados remotos y los nombres `:cloud` se excluyen. Si Ollama está apagado puedes
guardar nombres manuales; su disponibilidad se comprobará al utilizar la aplicación.
El modo multimodal necesita un modelo visual; en modo solo búsqueda puede dejarse
vacío el modelo de respuesta.

Este asistente configura servicios **locales** y rechaza URLs remotas, credenciales
en la URL y redirecciones. Para instalar un modelo usa por separado:

```text
ollama list
ollama pull NOMBRE_EXACTO_DEL_MODELO
```

El segundo comando sí descarga pesos. Elige un modelo adecuado para tu VRAM antes
de ejecutarlo. No se ejecuta automáticamente desde el asistente.

## 6. Ajustar la recuperación

- **Fragmentos:** 2/4/6/8 trozos de evidencia por pregunta; empieza por 4.
- **Solo búsqueda:** devuelve fuentes sin ejecutar generación.
- **Colección:** nombre del índice; una colección nueva permite separar otro corpus.
- **Tamaño / solapamiento:** caracteres, no tokens; predeterminado 500/80.
- **Contexto documental:** máximo de caracteres de las fuentes que recibe el LLM.
- **Tokens de salida:** límite de generación por respuesta.
- **Imágenes por respuesta:** entre 1 y 4; predeterminado 3.
- **Vistas por documento:** entre 1 y 500; cada página/figura nueva puede necesitar
  una descripción del modelo visual. Subir el límite aumenta el trabajo permitido.
- **Puerto web:** predeterminado 8765; se escucha en 127.0.0.1 por defecto.

No cambies el chunking sobre una colección ya creada. El asistente detecta esa
incompatibilidad y pide elegir una colección nueva. Cambiar modelos o el modo
multimodal puede requerir actualizar la indexación desde Biblioteca.

## 7. Revisar y guardar

La pantalla final muestra los archivos y valores que cambiarán. Marca la
confirmación y pulsa «Guardar configuración» para escribir:

- **`.env`:** parámetros de arranque y valores del CLI.
- **`data/settings.json`:** preferencias compartidas de la web.
- **`.cache/configuration-backups/<fecha>/`:** copia privada de los archivos anteriores.

Las escrituras son atómicas por archivo y se restauran los originales si el segundo
archivo falla. Conserva las claves adicionales de `.env` y las preferencias existentes.
No borra documentos, vectores, cachés ni modelos. Si hay trabajos RAG activos,
se bloquea el guardado y no se toca la configuración. No cancela ni reinicia procesos.

Un servidor abierto debe reiniciarse, cuando termine su trabajo, para leer los
cambios de `.env`. Guardar no arranca servicios ni reindexa.

## 8. Iniciar y usar la web

Pulsa «Iniciar web» al terminar, o usa los mismos lanzadores:

```powershell
.\Configurar.ps1 -Start
```

```bash
bash configurar.sh --start
```

El asistente usa el mismo controlador de segundo plano que los lanzadores siguientes.
Cerrar la consola o el navegador no detiene el servidor. Si ya está abierto en ese
puerto, se muestra su dirección real, incluida la IP de red, sin lanzar otra instancia.

Para controlar su ciclo de vida:

```powershell
.\Start-Web.ps1 start
.\Start-Web.ps1 status
.\Start-Web.ps1 restart
.\Start-Web.ps1 stop
```

En Linux: `bash web.sh start`, `bash web.sh status`, `bash web.sh restart` y
`bash web.sh stop`. Sin acción, ambos scripts arrancan el servidor. Leen el puerto
de `RAG_WEB_PORT` en `.env` (8765 por defecto); puedes indicar `-Port 8766` en
PowerShell o `--port 8766` en Linux.

El servidor en segundo plano sigue abierto al cerrar la consola. Su salida se
añade a `reports/rag-server-runtime.log` y `reports/rag-server-runtime-errors.log`;
también puedes verla en la sección Consola de la web. Start evita duplicados.
Stop/restart pide terminar o cancelar primero las tareas activas. Solo gestiona
el servidor web de este proyecto y conserva documentos, índices y Ollama.

Abre `http://127.0.0.1:8765` o el puerto elegido. En Configuración, prepara el
modelo de búsqueda; la primera vez se descarga a `.cache`. Después añade un TXT
pequeño desde Biblioteca y comprueba una pregunta con respuesta conocida. Activa
el procesamiento visual y prueba un PDF corto antes de enviar lotes grandes.

El servidor no carga modelos al arrancar. La preparación usa CPU; describir páginas
y responder sí puede utilizar la GPU mediante Ollama. La cola actual es secuencial:
las preguntas esperan mientras se indexa un lote. Fuentes consultadas permite revisar
las referencias. Toda la interfaz web, incluidas fuentes y librerías, se sirve localmente.

## Uso en la red local

La barra superior agrupa el estado real de Ollama, VRAM y acceso de red en un
desplegable, donde está «Recargar estado». Ctrl/Cmd+K busca documentos, prepara
una pregunta en el chat o cambia de sección. Los contadores de Biblioteca solo
aparecen cuando hay trabajo o errores; el pipeline sigue las tareas activas.

Los modelos de razonamiento disponen de `RAG_REASONING_MAX_NEW_TOKENS=2048` para
razonamiento y respuesta. Los demás mantienen `RAG_MAX_NEW_TOKENS=512`. La ventana
de contexto y el modelo seleccionado se conservan. Si el modelo termina sin texto
visible, el chat muestra un error específico y se guardan los datos de diagnóstico.

Para escuchar en una IP privada que tenga asignada este equipo:

```powershell
.\Start-Web.ps1 -Restart -Host 192.168.1.111 -WhenIdle
```

```bash
bash web.sh restart --host 192.168.1.111 --when-idle
```

Sustituye la IP por la tuya y abre `http://192.168.1.111:8765`. El reinicio espera
a que terminen las tareas. Si omites el host al reiniciar, conserva el que usa el
servidor. `-h` / `--help` muestra la ayuda; `status` consulta el estado. No se abren
reglas del cortafuegos automáticamente. Se rechazan IP públicas y direcciones comodín.
Los equipos de la LAN comparten biblioteca, ajustes y consola, sin cuentas o roles.
Consulta los [límites de seguridad](../SECURITY.md) y la [organización por temas](LIBRARY.md).

## Solución de problemas

| Síntoma | Comprobación |
|---|---|
| Python no encontrado o versión distinta | Instala Python 3.13 x64 y comprueba `py -3.13 --version` / `python3.13 --version`. |
| Linux no puede crear el entorno | Instala el soporte `venv` de esa versión de Python mediante tu distribución. |
| Ollama no disponible | Comprueba `ollama list`, su servidor y el puerto del asistente. |
| No hay modelo visual | Instala por separado uno con visión; actualizar la lista no descarga modelos. |
| Dependencias fallan | Consulta el registro; revisa SO, arquitectura, Python, conexión y espacio en disco. |
| No permite guardar o instalar | Termina las tareas y cierra el servidor si vas a modificar paquetes. |
| Un PDF tarda mucho | La descripción de páginas es inferencia; prueba primero documentos cortos y consulta Estadísticas. |
| Puerto ocupado por otro programa | Elige otro puerto; el asistente no termina procesos ajenos. |

Referencias: [Textual](https://textual.textualize.io/),
[PyTorch](https://pytorch.org/get-started/locally/) y [Ollama](https://docs.ollama.com/).

## Estado de validación

El asistente se ha probado en Windows con 12 tests de configuración y navegación
de terminal. Ambos lanzadores pasan su comprobación de sintaxis; el de Bash se ha
ejecutado en Git Bash y se comprueban las rutas Windows/Linux del entorno virtual.
Se ha verificado que PyTorch y PyMuPDF publican ruedas Linux x86_64 para esta
versión de Python. Falta ejecutar una instalación completa en Linux nativo y en un
Windows limpio. No se reinstaló el entorno de desarrollo mientras el RAG trabajaba.
