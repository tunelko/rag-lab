"""Read-only document previews, rendered locally without models or active content."""
from pathlib import Path
from urllib.parse import urlencode

TEXT_LIMIT = 512 * 1024
MAX_SIDE = 2000
IMAGES = {'.png', '.jpg', '.jpeg', '.webp'}


def resolve_document(root: Path, name: str) -> Path:
    root = root.resolve()
    path = (root / name).resolve()
    if not path.is_relative_to(root) or not path.is_file() or path.suffix.lower() not in {'.pdf', '.txt', '.md', '.markdown', *IMAGES}:
        raise FileNotFoundError('Documento no disponible.')
    return path


def _page(document, number):
    if document.is_encrypted:
        raise ValueError('No se puede previsualizar un documento cifrado.')
    if not 1 <= number <= document.page_count:
        raise ValueError('La página solicitada no existe en este documento.')
    return document.load_page(number - 1)


def describe(path: Path, name: str, page: int) -> dict:
    suffix = path.suffix.lower()
    if suffix in {'.txt', '.md', '.markdown'}:
        with path.open('rb') as source:
            content = source.read(TEXT_LIMIT + 1)
        return {'kind': 'text', 'file': path.name, 'text': content[:TEXT_LIMIT].decode('utf-8-sig', errors='replace'),
                'truncated': len(content) > TEXT_LIMIT}
    import pymupdf
    with pymupdf.open(path) as document:
        current = _page(document, page)
        return {'kind': 'pdf' if suffix == '.pdf' else 'image', 'file': path.name,
                'page': page, 'pages': document.page_count,
                'page_label': (current.get_label() or None) if suffix == '.pdf' else None,
                'url': '/api/source/page?' + urlencode({'file': name, 'page': page})}


def render_page(path: Path, page: int) -> bytes:
    import pymupdf
    if path.suffix.lower() not in {'.pdf', *IMAGES}:
        raise ValueError('Este documento tiene una vista de texto.')
    with pymupdf.open(path) as document:
        current = _page(document, page)
        side = max(current.rect.width, current.rect.height)
        if side <= 0:
            raise ValueError('La página no tiene dimensiones válidas.')
        scale = min(2, MAX_SIDE / side)
        return current.get_pixmap(matrix=pymupdf.Matrix(scale, scale),
                                  colorspace=pymupdf.csRGB, alpha=False).tobytes('png')
