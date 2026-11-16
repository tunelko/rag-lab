import test from 'node:test';
import assert from 'node:assert/strict';
import {ingestionState,selectIngestionJob} from '../../web/ingestion-state.js';

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

test('a live ingestion replaces a stale completed ingestion before the next status poll', () => {
  const old={id:'old',kind:'index',status:'done',progress:100,updated_at:'2026-11-16T10:00:00Z'};
  const live={id:'new',kind:'index',status:'running',progress:37,updated_at:'2026-11-16T10:01:00Z'};
  assert.equal(selectIngestionJob(old,[],live),live);
  assert.equal(ingestionState(selectIngestionJob(old,[],live)).percent,37);
});

test('the newest snapshot wins before comparing statuses of the same ingestion', () => {
  const stale={id:'same',kind:'index',status:'running',progress:40,updated_at:'2026-11-16T10:00:00Z'};
  const failed={...stale,status:'error',progress:45.2,updated_at:'2026-11-16T10:00:01Z'};
  assert.equal(selectIngestionJob(stale,[stale],failed),failed);
  assert.equal(ingestionState(selectIngestionJob(stale,[stale],failed)).percent,45.2);
  assert.equal(selectIngestionJob(failed,[],stale),failed);
});

test('a running ingestion takes priority over queued ingestion and live queries', () => {
  const running={id:'running',kind:'index',status:'running',updated_at:'2026-11-16T10:00:00Z'};
  const queued={id:'queued',kind:'index',status:'queued',updated_at:'2026-11-16T10:00:02Z'};
  const query={id:'query',kind:'query',status:'running',updated_at:'2026-11-16T10:00:03Z'};
  assert.equal(selectIngestionJob(queued,[running,queued],query),running);
});

test('queued ingestions retain FIFO order even when a later one receives a fresher update', () => {
  const first={id:'first',kind:'index',status:'queued',created_at:'2026-11-16T10:00:00Z',updated_at:'2026-11-16T10:00:04Z'};
  const last={id:'last',kind:'index',status:'queued',created_at:'2026-11-16T10:00:01Z',updated_at:'2026-11-16T10:00:05Z'};
  assert.equal(selectIngestionJob(last,[last,first]),first);
});

test('without active ingestions the latest result remains available and queries stay excluded', () => {
  const old={id:'old',kind:'index',status:'done',updated_at:'2026-11-16T10:00:00Z'};
  const latest={id:'latest',kind:'index',status:'cancelled',updated_at:'2026-11-16T10:00:01Z'};
  assert.equal(selectIngestionJob(old,[],latest),latest);
  assert.equal(selectIngestionJob(null,[],{kind:'query',status:'running'}),null);
});
