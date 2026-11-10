# Categories and subcategories

Library organization is virtual: original files stay in `data/documents/` and
their vector IDs, text, PDF page links and cached captions stay unchanged.
Assignments and topic names persist in `data/chroma/<collection>-topics.sqlite3`
(or the configured index directory). Back up this file alongside the index and
documents. Each collection has its own organization; it is never included in Git.

## Organizing documents

1. Open **Library / Biblioteca**, click **+** next to **Categories / Categorías** and name a category,
   for example Motherboards, Agriculture or Cybersecurity.
2. Open a category's **…** menu and choose **New subcategory / Nueva subcategoría**.
   The hierarchy has two levels. Documents can belong to a category or a subcategory.
3. Select document checkboxes (or **Select page / Seleccionar página**), choose a
   destination and click **Move / Mover**. A document has one location. Moving it
   never calls embeddings, captioning or generation.
4. Click a category/subcategory in the sidebar to filter the library and propose it
   as the upload destination. **Entire library / Toda la biblioteca** resets the filter;
   **Uncategorized / Sin categoría** shows documents without a category.
5. For uploads, review **Upload destination / Destino de la subida** above the file picker.
   Changing this destination updates files waiting to start in the current batch;
   files already uploading retain their destination. Byte-identical re-uploads
   keep the original document's topic and skip repeated processing.

Use the **…** menu to rename a category or subcategory. Delete only empty locations;
move their documents and remove empty subcategories first. These actions never delete PDFs.

## Choosing chat sources

The **Sources / Fuentes** selector chooses the entire library, a category including
its subcategories, one subcategory, or uncategorized documents. The browser remembers this
selection. It is independent of the library's display filter.

The server resolves document membership when it accepts the question. Focused
search applies a Chroma metadata filter before nearest-neighbor search. Global
questions scan every indexed document in that scope. Inventory questions count
only that scope. An empty or deleted topic never falls back to the whole library.
Moving files while a question waits in the queue cannot expand its accepted scope.

Each chat turn retains its chosen sources. Switching topics preserves visible
history, but dialogue from another topic is omitted from the next model request.
Reattempting a stopped/failed question retains its original topic.

These are organization and retrieval controls in a shared personal application,
not per-user access permissions. See [SECURITY.md](../SECURITY.md).
