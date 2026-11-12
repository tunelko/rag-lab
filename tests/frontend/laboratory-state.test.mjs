import test from 'node:test';
import assert from 'node:assert/strict';
import {activeExperiment, evidenceCoverage, evidenceOutcome} from '../../dist/laboratory-state.js';

test('headline excludes unannotated questions and retains the denominator on cancellation', () => {
  assert.deepEqual(evidenceCoverage({found: 1, annotated: 2, without_evidence: 3, completed: 5, total: 20}),
    {found: 1, annotated: 2, without: 3, completed: 5, total: 20, percent: 50});
  assert.equal(evidenceCoverage({without_evidence: 3}).percent, null);
  assert.equal(evidenceOutcome({evidence_found: false}), 'missing');
  assert.equal(evidenceOutcome({evidence_found: true}), 'found');
  assert.equal(evidenceOutcome({evidence_found: null}), 'unknown');
  assert.equal(evidenceOutcome({evidence_found: 'true'}), 'unknown');
});

test('only queued/running experiments block library controls', () => {
  assert.equal(activeExperiment({status: 'running'}), true);
  assert.equal(activeExperiment({status: 'queued'}), true);
  for (const status of ['done', 'cancelled', 'interrupted', 'error']) assert.equal(activeExperiment({status}), false);
});
