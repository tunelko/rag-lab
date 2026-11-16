import test from 'node:test';
import assert from 'node:assert/strict';
import {ingestionState} from '../../web/ingestion-state.js';

test('page counts and partial progress survive an ingestion error', () => {
  const reading={phase:'vision',page:181,completed:180,total:199,visuals_completed:180,visuals_total:199,cached_visuals:0};
  const state=ingestionState({kind:'index',status:'error',stage:'error',progress:45.2,reading_progress:reading});
  assert.equal(state.phase,'error');assert.equal(state.percent,45.2);assert.equal(state.pages.completed,180);
  assert.equal(ingestionState({kind:'query'}),null);
});

test('a backend without page counters never pretends a stalled zero is measured progress', () => {
  assert.equal(ingestionState({kind:'index',status:'running',stage:'chunking',progress:0}).percent,null);
  assert.equal(ingestionState({kind:'index',status:'running',stage:'chunking',progress:0,
    reading_progress:{completed:0,total:199}}).percent,0);
  assert.equal(ingestionState({kind:'index',status:'running',stage:'chunking',progress:0,
    reading_progress:{completed:200,total:199}}).pages,null);
});
