// Both status displays use the same real server counters, including cached pages.
export function ingestionState(job) {
  if (!job || job.kind !== 'index') return null;
  const reading = job.reading_progress;
  const count = value => Number.isSafeInteger(value) && value >= 0;
  const pages = reading && count(reading.total) && reading.total > 0
    && count(reading.completed) && reading.completed <= reading.total ? reading : null;
  const known = Number.isFinite(job.progress)
    && !(job.stage === 'chunking' && job.progress === 0 && !pages && job.status === 'running');
  return {percent: known ? Math.max(0, Math.min(100, job.progress)) : null,
    document: job.current_document || job.document_name || '', pages,
    phase: job.status === 'queued' ? 'queued' : job.status === 'done' ? 'done'
      : job.status === 'cancelled' ? 'cancelled' : job.status === 'error' ? 'error'
      : job.stage === 'indexing' ? 'indexing' : !pages ? 'preparing'
      : pages.phase === 'vision' ? 'vision' : 'reading'};
}
