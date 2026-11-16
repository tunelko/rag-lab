// Both status displays use the same real server counters, including cached pages.
export function selectIngestionJob(reported, jobs = [], live = null) {
  const timestamp = (job, field = 'updated_at') => {
    const value = Date.parse(job[field] || job.updated_at || job.created_at);
    return Number.isFinite(value) ? value : 0;
  };
  const snapshots = new Map();
  for (const job of [reported, ...jobs, live]) {
    if (job?.kind !== 'index') continue;
    const key = job.id ?? job, previous = snapshots.get(key);
    if (!previous || timestamp(job) >= timestamp(previous)) snapshots.set(key, job);
  }
  const priority = job => job.status === 'running' ? 0 : job.status === 'queued' ? 1 : 2;
  return [...snapshots.values()].sort((a, b) => priority(a) - priority(b)
    || (a.status === 'queued' ? timestamp(a, 'created_at') - timestamp(b, 'created_at')
      : timestamp(b) - timestamp(a)))[0] ?? null;
}

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
