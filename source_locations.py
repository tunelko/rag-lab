"""Read PDF page labels without changing indexed physical page numbers."""

from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=64)
def _pdf_labels(path: str, size: int, modified_ns: int) -> tuple[str, ...]:
    # The file signature invalidates cached labels when a document is replaced.
    import pymupdf

    with pymupdf.open(path) as pdf:
        if pdf.is_encrypted or not pdf.get_page_labels():
            return ()
        return tuple(page.get_label() for page in pdf)


def pdf_page_label(path: Path, page: int) -> str | None:
    """Return the PDF's declared label; never guess it from OCR or a caption."""
    if path.suffix.lower() != ".pdf" or not isinstance(page, int) or page < 1:
        return None
    try:
        stat = path.stat()
        labels = _pdf_labels(str(path), stat.st_size, stat.st_mtime_ns)
        return labels[page - 1] or None
    except (OSError, RuntimeError, ValueError, IndexError):
        # A missing label must not prevent retrieval of existing evidence.
        return None
