// Values only: no DOM, networking or inferred correctness.
export const activeExperiment = value => ['queued', 'running'].includes(value?.status);
export const evidenceOutcome = row => row?.evidence_found === true ? 'found' : row?.evidence_found === false ? 'missing' : 'unknown';
export function evidenceCoverage(summary = {}) {
  const count = value => Number.isInteger(value) && value >= 0 ? value : 0;
  const annotated = count(summary.annotated), found = count(summary.found);
  return {found, annotated, completed: count(summary.completed), total: count(summary.total),
    without: count(summary.without_evidence), percent: annotated ? 100 * found / annotated : null};
}
