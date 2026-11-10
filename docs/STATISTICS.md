# Local statistics history

Statistics is stored in `data/statistics.sqlite3`, separate from ChromaDB and
ignored by Git. Closing the page or restarting RAG preserves the saved history.

- **Retention:** seven days, automatically pruned; views for 1 hour, 1 day and
  1 week, plus the existing short live windows.
- **Collection:** the running server samples hardware in the background every
  10 seconds even without an open browser. The visible live view can sample every
  2 seconds. The archive keeps one actual reading approximately every 30 seconds
  (about 20,160 readings per week), rather than storing screenshots or model data.
- **Charts:** long windows show a bounded selection of actual points. Min/max/mean
  use all archived readings in the selected window. Missing periods and unavailable
  sensors remain gaps; no synthetic zero readings are inserted. Dates appear in
  tooltips for day/week views.
- **Queries:** question, timestamp, model, tokens, tok/s, queue and execution times
  survive restarts. Day/week filters and pagination expose older records.
- **Indexing:** a collapsed history table records documents, timestamps, duration,
  queue time, chunk counts and status in the same interval.

History starts when collection is enabled. An upgrade can import only measurements
and jobs still available in the previous server's memory; earlier unrecorded periods
cannot be reconstructed. While the server is stopped, it collects no samples.
Unfinished runs after a restart are labeled **Interrumpida**.

Completed indexing jobs can still be polled after restart. The upload queue also
reconciles interrupted entries against the actual library: an already indexed PDF
is recovered without uploading or indexing it again. Expired local session tokens
are refreshed once for requests rejected before execution.

For a long upgrade during indexing, `python -m tools.history_bridge` preserves the
old server's available telemetry and job metrics without loading models. It exits
when the server provides native persistent history. Normal stop/restart imports
available legacy metrics once before shutdown.

To restart after the active queue finishes:

```powershell
.\Start-Web.ps1 --restart --when-idle
```

```bash
bash web.sh restart --when-idle
```

The wait keeps the original server running. If its process changes during the wait,
the deferred action exits instead of restarting another instance.
