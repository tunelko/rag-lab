"""Texts of the setup wizard in Spanish and English. Standard library only.

Every message the wizard shows or raises is looked up here by key, so a language is one
dictionary. `text` falls back to Spanish, then to the key, and never raises for a missing
translation.
"""
from __future__ import annotations

import locale
import os

LANGUAGES = {'es': 'Español', 'en': 'English'}
DEFAULT = 'es'

TEXTS = {
    'es': {
        # Errors and notices raised outside the terminal interface
        'endpoint_local': 'Usa una URL local: http://127.0.0.1:11434 (sin credenciales ni rutas).',
        'endpoint_port': 'Puerto de Ollama no válido.',
        'redirected': 'El servicio local intentó redirigir la petición.',
        'bad_profile_or_mode': 'Perfil o modo de respuesta no válido.',
        'bad_model_name': 'Nombre de modelo no válido.',
        'cloud_model': 'Este asistente configura modelos locales; elige uno sin :cloud.',
        'need_response_model': 'Elige un modelo de respuesta o activa solo búsqueda.',
        'need_vision_model': 'El modo multimodal necesita un modelo visual.',
        'bad_collection': 'La colección necesita entre 3 y 63 letras, números, guiones o guiones bajos.',
        'bad_chunks': 'Fragmentos: 100–4000 caracteres; solapamiento menor que el tamaño.',
        'bad_limits': 'Revisa los límites de recuperación, imágenes, contexto, salida y puerto.',
        'index_differs': 'El índice existente usa otra división o embeddings. Elige una colección nueva.',
        'metadata_failed': 'No se han podido validar los metadatos de {model}',
        'ollama_unavailable': 'Ollama no disponible: {error}',
        'bad_server_state': 'Estado del servidor no válido.',
        'bad_profile': 'Perfil no válido.',
        'migrate_first': 'Migra primero la colección {name}: python -m tools.migrate_vectors --collection {name}. No se han cambiado las dependencias.',
        'need_python': 'La instalación fijada requiere Python 3.13 x64 / x86_64.',
        'server_open': 'El RAG está abierto. Cierra su servidor antes de instalar dependencias; puedes seguir revisando el asistente.',
        'environment_kept': 'Entorno existente: se conserva .venv.',
        'install_failed': 'La instalación falló; revisa el registro. No se ha guardado la configuración.',
        'jobs_active': 'Hay tareas activas. Espera a que terminen antes de aplicar cambios; no se ha escrito ningún archivo.',
        'model_not_installed': 'El modelo de respuesta no está instalado en Ollama.',
        'vision_not_installed': 'El modelo visual debe estar instalado y declarar capacidad vision.',
        'already_open': 'El RAG ya está abierto: {url}',
        'install_first': 'Primero instala el entorno desde el asistente.',
        'preparing_interface': 'Preparando interfaz de terminal en .cache/wizard-venv (sin tocar .venv)…',
        'unsupported_system': 'Sistemas admitidos: Windows y Linux.',
        'use_python': 'Usa Python 3.13 x64. Linux puede necesitar el paquete python3.13-venv.',
        'need_terminal': 'Abre una terminal interactiva o usa --check para la detección de solo lectura.',
        'need_launcher': 'Abre Configurar.ps1 o configurar.sh para instalar la interfaz aislada.',
        # Terminal interface
        'title': 'RAG Local · Configuración',
        'subtitle': 'Laboratorio local / Windows + Linux',
        'heading': 'π > RAG LOCAL  /  CONFIGURACIÓN DEL LABORATORIO',
        'key_hints': 'Ctrl+→ Siguiente  ·  Ctrl+← Anterior  ·  Ctrl+Q Salir',
        'step_language': 'Idioma', 'step_welcome': 'Bienvenida', 'step_environment': 'Entorno Python',
        'step_models': 'Modelos Ollama', 'step_retrieval': 'Recuperación RAG', 'step_review': 'Revisar y guardar',
        'step_done': 'Listo',
        'language_title': 'Idioma · Language',
        'language_description': 'Elige el idioma del asistente. Los textos cambian al momento y la elección se recuerda.\n\n'
                                'Choose the language of this wizard. Texts change at once and the choice is remembered.',
        'language_label': 'Idioma del asistente',
        'language_changed': 'Idioma del asistente: {name}.',
        'help_language': 'Esta elección solo afecta al asistente y a sus mensajes en la consola.\n\n'
                         'La interfaz web tiene su propio selector de idioma y detecta el del navegador.',
        'help_welcome': 'TAB cambia el foco. Las flechas recorren las opciones. Ctrl+→ avanza y Ctrl+← retrocede.\n\n'
                        'Los cambios se guardan únicamente en Revisar y guardar. Salir antes conserva la configuración actual.',
        'help_environment': 'Ollama usa su propio soporte GPU. El perfil recomendado instala PyTorch CPU para los embeddings.\n\n'
                            'CUDA añade el backend Transformers/NF4. No se actualizan drivers ni se ejecutan modelos. '
                            'La instalación necesita Internet y espacio para los paquetes.',
        'help_models': 'Actualizar consulta /api/tags y /api/show: solo metadatos. No descarga, carga ni ejecuta modelos.\n\n'
                       'Si Ollama está apagado puedes escribir los nombres y guardar. Para descargar un modelo, usa ollama pull por separado.',
        'help_retrieval': 'Un fragmento es un trozo de texto recuperado como evidencia. Más fragmentos amplían el contexto y el trabajo.\n\n'
                          'La ingesta visual describe páginas completas. Un límite alto permite más trabajo, no garantiza que sea rápido. '
                          'Si cambias la división de un índice existente, elige otra colección.',
        'help_review': 'Guardar escribe .env y data/settings.json, con copia de seguridad privada en .cache.\n\n'
                       'No reindexa ni arranca servicios. Con tareas activas el guardado se bloquea. '
                       'Un servidor ya abierto debe reiniciarse después para leer cambios de .env.',
        'help_done': 'La instalación y la configuración están separadas del arranque. Iniciar web vuelve a la consola y ejecuta '
                     'el servidor en primer plano, donde Ctrl+C lo cierra. No se detienen instancias existentes.',
        'welcome_title': 'Tu laboratorio, paso a paso',
        'welcome_description': 'Configura un RAG local con Ollama, documentos y fuentes.\n\n'
                               'Este asistente conserva los documentos, el índice y los modelos existentes. '
                               'Puedes recorrerlo sin aplicar cambios.\n\n'
                               'Dos lanzadores; el mismo asistente en Windows y Linux.',
        'check_machine': 'Comprobar equipo',
        'environment_title': '01 / Entorno Python',
        'profile_ollama': 'Ollama GPU + embeddings CPU (recomendado)',
        'profile_cuda': 'PyTorch CUDA 13.0 + HF NF4 (avanzado)',
        'environment_description': 'Requiere Python 3.13 x64 / Linux x86_64. Los paquetes se instalan en .venv; '
                                   'el asistente usa otro entorno en .cache.',
        'install_dependencies': 'Instalar dependencias',
        'commands_to_run': 'COMANDOS A EJECUTAR',
        'models_title': '02 / Modelos de respuesta',
        'ollama_server': 'Servidor Ollama local',
        'refresh_models': 'Actualizar modelos',
        'text_model': 'Modelo de texto instalado / nombre manual',
        'type_name_below': 'Escribir nombre debajo',
        'model_placeholder': 'Nombre exacto de ollama list',
        'enable_multimodal': 'Activar documentos e imágenes multimodales',
        'vision_model': 'Modelo visual instalado / nombre manual',
        'model_choice': 'Elección del modelo por consulta',
        'mode_automatic': 'Automática según pregunta y fuentes', 'mode_manual': 'Manual',
        'retrieval_title': '03 / Recuperación y documentos',
        'fragments': 'Fragmentos recuperados por pregunta',
        'search_only': 'Solo buscar fragmentos, sin generar respuesta',
        'field_collection': 'Colección del índice',
        'field_chunk_size': 'Tamaño del fragmento (caracteres)',
        'field_overlap': 'Solapamiento (caracteres)',
        'field_context': 'Contexto documental máximo (caracteres)',
        'field_output': 'Tokens máximos de salida',
        'field_images': 'Imágenes por respuesta (1–4)',
        'field_visuals': 'Vistas por documento (1–500)',
        'field_port': 'Puerto de la web local',
        'review_title': '04 / Revisión de cambios',
        'confirm_changes': 'He revisado los cambios y quiero guardarlos',
        'save_configuration': 'Guardar configuración',
        'done_title': 'Configuración guardada',
        'done_description': 'Siguiente: inicia la web, prepara los embeddings desde Configuración y añade '
                            'documentos en Biblioteca. La primera preparación descarga el modelo de búsqueda. '
                            'Las consultas y la indexación comparten una cola.',
        'start_web': 'Iniciar web',
        'previous': 'Anterior', 'next': 'Siguiente →',
        'status_review': 'Revisión · Sin cambios aplicados',
        'need_integer': 'El campo «{field}» necesita un número entero.',
        'review_files': 'ARCHIVOS\n.env\ndata/settings.json\nCopia privada: .cache/configuration-backups/',
        'review_changes': 'CAMBIOS',
        'review_unchanged': 'Los valores elegidos coinciden con los existentes.',
        'review_summary': 'Respuesta: {response}\nVisual: {vision}\nWeb: {url}\n\nGuardar no instala paquetes ni ejecuta modelos.',
        'only_search': 'solo búsqueda', 'not_chosen': 'sin elegir',
        'wait_operation': 'Espera a que termine la operación antes de cerrar.',
        'detection': 'Sistema: {system}\nPython: {python}\nEntorno RAG: {environment}\nOllama: {ollama}\n'
                     'Modelos: {models} · Visuales: {visuals}\nServidores RAG abiertos: {servers}\nTareas activas: {jobs}',
        'environment_present': 'existente', 'environment_missing': 'pendiente de instalar',
        'ollama_connected': 'conectado', 'ollama_missing': 'no disponible',
        'detection_done': 'Detección completada. Solo se han consultado metadatos locales.',
        'dependencies_ready': 'Dependencias listas. No se ha ejecutado ningún modelo.',
        'status_installed': 'Instalación terminada · Continúa la configuración',
        'status_stopped': 'Operación detenida · Revisa el registro',
        'finished': 'Web: {url}\nCopia: {backup}\n\nWindows: .\\Configurar.ps1 -Start\nLinux: bash configurar.sh --start',
        'configuration_saved': 'Configuración guardada. Documentos, índice y modelos conservados.',
        'status_done': 'Listo · Arranque pendiente',
        'need_confirmation': 'Revisa los cambios y marca la confirmación antes de guardar.',
        'status_installing': 'Instalando dependencias…', 'status_saving': 'Guardando configuración…',
    },
    'en': {
        'endpoint_local': 'Use a local URL: http://127.0.0.1:11434 (no credentials or paths).',
        'endpoint_port': 'Invalid Ollama port.',
        'redirected': 'The local service tried to redirect the request.',
        'bad_profile_or_mode': 'Invalid profile or answer mode.',
        'bad_model_name': 'Invalid model name.',
        'cloud_model': 'This wizard configures local models; choose one without :cloud.',
        'need_response_model': 'Choose an answer model or enable search only.',
        'need_vision_model': 'Multimodal mode needs a vision model.',
        'bad_collection': 'The collection needs 3 to 63 letters, digits, hyphens or underscores.',
        'bad_chunks': 'Fragments: 100–4000 characters; the overlap must be smaller than the size.',
        'bad_limits': 'Check the limits for retrieval, images, context, output and port.',
        'index_differs': 'The existing index uses another chunking or embedding model. Choose a new collection.',
        'metadata_failed': 'Could not validate the metadata of {model}',
        'ollama_unavailable': 'Ollama is not available: {error}',
        'bad_server_state': 'Invalid server state.',
        'bad_profile': 'Invalid profile.',
        'migrate_first': 'Migrate the collection {name} first: python -m tools.migrate_vectors --collection {name}. Dependencies were not changed.',
        'need_python': 'The pinned installation requires Python 3.13 x64 / x86_64.',
        'server_open': 'RAG Local is running. Stop its server before installing dependencies; you can keep reviewing the wizard.',
        'environment_kept': 'Existing environment: .venv is kept.',
        'install_failed': 'The installation failed; check the log. The configuration was not saved.',
        'jobs_active': 'There are active jobs. Wait for them to finish before applying changes; no file was written.',
        'model_not_installed': 'The answer model is not installed in Ollama.',
        'vision_not_installed': 'The vision model must be installed and declare the vision capability.',
        'already_open': 'RAG Local is already running: {url}',
        'install_first': 'Install the environment from the wizard first.',
        'preparing_interface': 'Preparing the terminal interface in .cache/wizard-venv (.venv is not touched)…',
        'unsupported_system': 'Supported systems: Windows and Linux.',
        'use_python': 'Use Python 3.13 x64. Linux may need the python3.13-venv package.',
        'need_terminal': 'Open an interactive terminal, or use --check for read-only detection.',
        'need_launcher': 'Run Configurar.ps1 or configurar.sh to install the isolated interface.',
        'title': 'RAG Local · Setup',
        'subtitle': 'Local laboratory / Windows + Linux',
        'heading': 'π > RAG LOCAL  /  LABORATORY SETUP',
        'key_hints': 'Ctrl+→ Next  ·  Ctrl+← Back  ·  Ctrl+Q Quit',
        'step_language': 'Language', 'step_welcome': 'Welcome', 'step_environment': 'Python environment',
        'step_models': 'Ollama models', 'step_retrieval': 'RAG retrieval', 'step_review': 'Review and save',
        'step_done': 'Done',
        'language_title': 'Idioma · Language',
        'language_description': 'Choose the language of this wizard. Texts change at once and the choice is remembered.\n\n'
                                'Elige el idioma del asistente. Los textos cambian al momento y la elección se recuerda.',
        'language_label': 'Wizard language',
        'language_changed': 'Wizard language: {name}.',
        'help_language': 'This choice only affects the wizard and its console messages.\n\n'
                         'The web interface has its own language selector and detects the browser language.',
        'help_welcome': 'TAB moves the focus. Arrow keys move through options. Ctrl+→ goes forward and Ctrl+← goes back.\n\n'
                        'Changes are saved only in Review and save. Quitting earlier keeps the current configuration.',
        'help_environment': 'Ollama uses its own GPU support. The recommended profile installs CPU PyTorch for embeddings.\n\n'
                            'CUDA adds the Transformers/NF4 backend. No drivers are updated and no models are run. '
                            'The installation needs Internet access and disk space for the packages.',
        'help_models': 'Refresh queries /api/tags and /api/show: metadata only. It does not download, load or run models.\n\n'
                       'If Ollama is off you can type the names and save. To download a model, use ollama pull separately.',
        'help_retrieval': 'A fragment is a piece of text retrieved as evidence. More fragments widen the context and the work.\n\n'
                          'Visual ingestion describes whole pages. A high limit allows more work; it does not make it fast. '
                          'If you change the chunking of an existing index, choose another collection.',
        'help_review': 'Saving writes .env and data/settings.json, with a private backup in .cache.\n\n'
                       'It does not reindex or start services. Saving is blocked while jobs are active. '
                       'A server that is already running must be restarted afterwards to read changes in .env.',
        'help_done': 'Installation and configuration are separate from starting. Start web returns to the console and runs '
                     'the server in the foreground, where Ctrl+C stops it. Existing instances are not stopped.',
        'welcome_title': 'Your laboratory, step by step',
        'welcome_description': 'Set up a local RAG with Ollama, documents and sources.\n\n'
                               'This wizard keeps existing documents, index and models. '
                               'You can go through it without applying changes.\n\n'
                               'Two launchers; the same wizard on Windows and Linux.',
        'check_machine': 'Check this computer',
        'environment_title': '01 / Python environment',
        'profile_ollama': 'Ollama GPU + CPU embeddings (recommended)',
        'profile_cuda': 'PyTorch CUDA 13.0 + HF NF4 (advanced)',
        'environment_description': 'Requires Python 3.13 x64 / Linux x86_64. Packages are installed in .venv; '
                                   'the wizard uses another environment in .cache.',
        'install_dependencies': 'Install dependencies',
        'commands_to_run': 'COMMANDS TO RUN',
        'models_title': '02 / Answer models',
        'ollama_server': 'Local Ollama server',
        'refresh_models': 'Refresh models',
        'text_model': 'Installed text model / typed name',
        'type_name_below': 'Type the name below',
        'model_placeholder': 'Exact name from ollama list',
        'enable_multimodal': 'Enable multimodal documents and images',
        'vision_model': 'Installed vision model / typed name',
        'model_choice': 'Model choice per question',
        'mode_automatic': 'Automatic, from the question and sources', 'mode_manual': 'Manual',
        'retrieval_title': '03 / Retrieval and documents',
        'fragments': 'Fragments retrieved per question',
        'search_only': 'Only search fragments, without generating an answer',
        'field_collection': 'Index collection',
        'field_chunk_size': 'Fragment size (characters)',
        'field_overlap': 'Overlap (characters)',
        'field_context': 'Maximum document context (characters)',
        'field_output': 'Maximum output tokens',
        'field_images': 'Images per answer (1–4)',
        'field_visuals': 'Views per document (1–500)',
        'field_port': 'Local web port',
        'review_title': '04 / Review of changes',
        'confirm_changes': 'I have reviewed the changes and want to save them',
        'save_configuration': 'Save configuration',
        'done_title': 'Configuration saved',
        'done_description': 'Next: start the web, prepare the embeddings from Settings and add '
                            'documents in Library. The first preparation downloads the retrieval model. '
                            'Questions and indexing share one queue.',
        'start_web': 'Start web',
        'previous': 'Back', 'next': 'Next →',
        'status_review': 'Review · No changes applied',
        'need_integer': 'The field “{field}” needs a whole number.',
        'review_files': 'FILES\n.env\ndata/settings.json\nPrivate backup: .cache/configuration-backups/',
        'review_changes': 'CHANGES',
        'review_unchanged': 'The chosen values match the existing ones.',
        'review_summary': 'Answer: {response}\nVision: {vision}\nWeb: {url}\n\nSaving does not install packages or run models.',
        'only_search': 'search only', 'not_chosen': 'not chosen',
        'wait_operation': 'Wait for the operation to finish before closing.',
        'detection': 'System: {system}\nPython: {python}\nRAG environment: {environment}\nOllama: {ollama}\n'
                     'Models: {models} · Vision: {visuals}\nRAG servers running: {servers}\nActive jobs: {jobs}',
        'environment_present': 'present', 'environment_missing': 'not installed yet',
        'ollama_connected': 'connected', 'ollama_missing': 'not available',
        'detection_done': 'Detection finished. Only local metadata was read.',
        'dependencies_ready': 'Dependencies ready. No model was run.',
        'status_installed': 'Installation finished · Continue with the configuration',
        'status_stopped': 'Operation stopped · Check the log',
        'finished': 'Web: {url}\nBackup: {backup}\n\nWindows: .\\Configurar.ps1 -Start\nLinux: bash configurar.sh --start',
        'configuration_saved': 'Configuration saved. Documents, index and models kept.',
        'status_done': 'Done · Not started yet',
        'need_confirmation': 'Review the changes and tick the confirmation before saving.',
        'status_installing': 'Installing dependencies…', 'status_saving': 'Saving configuration…',
    },
}


def text(key, language=DEFAULT, **values):
    """The message for `key` in `language`, with `{name}` placeholders filled from `values`."""
    template = TEXTS.get(language, {}).get(key) or TEXTS[DEFAULT].get(key) or key
    return template.format(**values) if values else template


def normalize(value):
    """'es' or 'en' from a language code such as es_ES, en-GB or Spanish_Spain; None when it is neither."""
    name = str(value or '').strip().lower()
    for code, prefixes in (('es', ('es', 'spanish', 'español', 'espanol', 'castellano')), ('en', ('en', 'english'))):
        if name == code or any(name.startswith(prefix) and not name[len(prefix):len(prefix) + 1].isalpha()
                               for prefix in prefixes):
            return code
    return None


def system_language(environ=None):
    """The language of the user's session, from the usual variables and then the system locale."""
    environ = os.environ if environ is None else environ
    for name in ('LC_ALL', 'LC_MESSAGES', 'LANGUAGE', 'LANG'):
        found = normalize(environ.get(name, '').split(':')[0])
        if found:
            return found
    try:
        if os.name == 'nt':
            import ctypes
            identifier = ctypes.windll.kernel32.GetUserDefaultUILanguage()
            found = normalize(locale.windows_locale.get(identifier, ''))
            if found:
                return found
        return normalize(locale.getlocale()[0])
    except (AttributeError, OSError, ValueError):
        return None


def resolve(requested=None, saved=None, environ=None):
    """Which language to use: an explicit request, then the saved choice, then the session's, then English."""
    environ = os.environ if environ is None else environ
    return (normalize(requested) or normalize(environ.get('RAG_SETUP_LANGUAGE')) or normalize(saved)
            or system_language(environ) or 'en')
