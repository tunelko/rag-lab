"""Direct Ollama transport: no ambient proxy and no redirect of document context."""
import urllib.error
import urllib.request


class NoOllamaRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, target):
        raise urllib.error.URLError('Ollama intentó redirigir la petición; no se reenviará el contexto a otra dirección.')


def open_ollama(request, timeout=600):
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoOllamaRedirect())
    return opener.open(request,timeout=timeout)
