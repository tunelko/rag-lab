# Repositorio local y futura publicación

Repositorio: [tunelko/rag-lab](https://github.com/tunelko/rag-lab), con remoto HTTPS
sin credenciales en la URL. Se conserva el historial existente. El código puede
probarse en local antes de enviar nuevos commits.

## Qué se versiona

Código Python y web, los dos lanzadores del asistente, versiones de dependencias,
`.env.example`, documentación, tests y librerías/fuentes vendorizadas con sus licencias.
`dist` contiene código fuente web necesario: no debe excluirse como si fuese un
directorio de compilación desechable.

## Qué permanece privado y local

`.env`, preferencias reales, documentos, índices, imágenes extraídas, modelos,
entornos virtuales, cachés, copias de configuración, informes y registros. También
se excluyen los directorios de manuales copiados localmente en la raíz.

`.gitignore` protege archivos no versionados. No elimina archivos del disco ni
retira automáticamente un archivo que ya estuviese en el historial. La revisión
inicial no encontró PDFs, modelos, `.env`, preferencias o bases vectoriales
versionados ni patrones de credenciales en los archivos de texto seguidos por Git;
no equivale a una garantía sobre todas las credenciales posibles.

## Comprobar el estado

```text
git status --short
git ls-files
git remote -v
git log -5 --oneline
```

Para comprobar una exclusión concreta:

```text
git check-ignore -v .env data/settings.json data/documents/ejemplo.pdf
```

El repositorio de GitHub es público. La licencia del código propio está pendiente
de elección; por ahora no se asigna una licencia a tu trabajo. Marked, DOMPurify,
Textual y las fuentes conservan sus propias licencias; la aplicación no incluye
pesos de modelos ni derechos sobre los documentos que cada usuario añada.

## Pruebas del asistente sin GPU

Con la interfaz ya preparada en Windows:

```powershell
.\.cache\wizard-venv\Scripts\python.exe -m unittest -v tests.unit.test_configuration
```

En Linux:

```bash
.cache/wizard-venv/bin/python -m unittest -v tests.unit.test_configuration
```

Los tests usan directorios temporales y servicios falsos. Comprueban la navegación,
guardado, recuperación ante fallo y bloqueo con trabajos activos. No instalan
dependencias del RAG, descargan modelos ni llaman a Ollama.

## Actualizar el remoto

Desde esta misma carpeta puedes comprobar el remoto y enviar commits revisados:

```text
git remote -v
git push origin main
```

La autenticación se resuelve mediante el gestor de credenciales o GitHub CLI.
No añadas tokens a `.env`, scripts, comandos de remoto ni archivos de CI. Actions
usa su credencial efímera con permisos de lectura; no requiere tu token personal.
No hace falta recrear el repositorio ni hacer `git init` de nuevo.

Para crear un ZIP solo con archivos del commit, después de revisar su contenido:

```text
git archive --format=zip --output=reports/rag-local-source.zip HEAD
```

Crea antes `reports` si no existe. `.gitattributes` excluye del ZIP el material
histórico del rediseño y las notas de trabajo; siguen presentes en el historial Git.
El ZIP no incluye cambios sin commit ni los datos locales ignorados.
