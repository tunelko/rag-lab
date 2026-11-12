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
search applies a SQLite file filter before nearest-neighbor search. Global
questions scan every indexed document in that scope. Inventory questions count
only that scope. An empty or deleted topic never falls back to the whole library.
Moving files while a question waits in the queue cannot expand its accepted scope.
Queued queries and index jobs also retain the preferences captured at submission.

Each chat turn retains its chosen sources. Switching topics preserves visible
history, but dialogue from another topic is omitted from the next model request.
Reattempting a stopped/failed question retains its original topic.

These are organization and retrieval controls in a shared personal application,
not per-user access permissions. See [SECURITY.md](../SECURITY.md).

## Index publication and recovery

A collection-scoped OS lock excludes simultaneous CLI/web writers. New vectors
are staged first; a flushed, atomically replaced manifest publishes their IDs.
Focused and global retrieval read only published IDs. Old vectors are collected
after publication. A failed write/cancellation preserves the previous published
version; a hard interruption or failed cleanup can leave invisible orphan vectors
on disk. This is not a distributed transaction or a resumable execution queue.

The upload API indexes only the files in its accepted batch. Browser recovery
requires a server-confirmed document identity; matching a filename and size alone
cannot prove an interrupted upload succeeded. An unconfirmed upload stays retryable
instead of being silently marked complete.

There is no per-document delete/trash operation in the API or UI. Removing
an upload queue entry does not remove its original file or its published vectors.
The only way to take individual documents out is to withdraw a tender (below),
which moves its files to a backup folder.

## Tender documents (pliegos)

A profile can name a public source of tenders in `profiles/<profile>/pliegos.json`:
a syndication feed, the hosts that serve its documents, a subject filter and the
category its tenders are filed under. When at least one profile has such a file,
the Library header shows **Find tenders / Buscar pliegos**. Nothing is fetched
until the user asks, and it happens in two separate steps:

1. **Read the platform.** The server reads the feed, newest first, back to the
   chosen period (1, 3, 7, 15 or 30 days) and keeps the tenders the profile is about in
   a local listing, `data/pliegos/catalog-<profile>.json`. Only the listing is
   read: no document is downloaded. A later reading stops where the previous one
   ended, so it is short. The reading runs beside the job queue and does not keep
   questions waiting; it can be stopped, and what it found stays listed.
2. **Search, choose, download.** The dialog has two tabs: the tenders of the
   listing that are not downloaded yet, and the ones in the library. Text and
   region narrow the tab; the state buttons (open, deadline over but not awarded,
   awarded or concluded, all)
   show how many tenders are in each state. An empty result says which choice
   left it empty and offers the way out, such as reading a longer period. The
   chosen tenders (up to 25 at a time) are downloaded as one job:
   their technical and administrative PDFs go to `data/documents/`, each tender
   gets a summary sheet `<file number>__FICHA.md` with what the platform publishes
   (body, subject, state, deadline, region, budget, link), everything is filed
   under the profile's category and indexed. A tender whose technical document
   cannot be downloaded is skipped and reported.

"Open" in the search means in its submission period on the server's date today.
The sheet states the date of its data instead, because it is indexed text. When a
reading finds a newer state for a tender that is in the library, its sheet is
rewritten and indexed again.

`data/pliegos/manifest.json` is the register of the tenders in the library.
**Withdraw / Retirar** moves the chosen tenders' PDFs and sheets to
`data/library-backups/<YYYYMMDD-HHMMSS>/documents/`, removes them from the
manifest and deletes their vectors; `retirados.json` in that folder records what
was withdrawn. To bring them back, copy the files to `data/documents/` and update
the index. Withdrawing is refused while any job runs and from any computer other
than the one running RAG Local.

Network use is limited to this feature and to the hosts written in the profile,
over HTTPS, without proxies, with certificate verification and the identifier
`RAG-Local/1.0`. Redirects to any other host are refused. Indexing stays local.

## Emptying the library

**Empty library / Vaciar biblioteca**, in the Library header, starts over without
deleting anything. After typing the confirmation word, the server moves the whole
library into one new dated folder, `data/library-backups/<YYYYMMDD-HHMMSS>/`:

| Backup folder | Taken from |
| --- | --- |
| `documents/` | every file in `data/documents/` |
| `index/` | the collection's manifest, vector database and categories (`data/chroma/<collection>-*`) |
| `visual/` | rendered figures and cached image descriptions (`data/visual/`) |

The library is then empty and ready for new uploads. Settings, saved credentials
and statistics are not touched, and other collections keep their own index files.
Switch on **Keep the image descriptions already computed** to leave `data/visual/`
in place: the cache is addressed by image content, so uploading the same documents
again does not call the vision model for pages it has already described.

The operation is refused while any job is running or queued, while another process
is writing the index, and from any computer other than the one running RAG Local.
The manifest is moved first, so an interruption leaves an unpublished index rather
than a half-published one. If a file is open elsewhere (Windows refuses to move it),
everything already moved is put back and nothing changes.

Emptying is also the way to reuse a collection name after changing the embedding
model or chunking in `.env`: the new index is created with the new configuration.

To restore a backup, stop the server and move the three folders' contents back to
their original locations, replacing what is there; each backup includes a short
note with these steps. Backups are never removed automatically: delete old ones
by hand to recover disk space.
