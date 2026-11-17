// How a question travelled through retrieval and the model, read from the job the browser already polls.
// Pure: no DOM and no texts. chat.js turns the result into the side panel and its checklist.
import {groupSources, relevance} from './chat-sources.js';

const live = status => ['queued', 'running'].includes(status);

// Citations written in the answer that point to a retrieved fragment.
export function citedCount(answer, sources = []) {
  const known = new Set(sources.map(source => Number(source.citation))), found = new Set();
  for (const match of String(answer || '').matchAll(/\[(\d+)\]/g)) if (known.has(Number(match[1]))) found.add(Number(match[1]));
  return found.size;
}

/* phase: empty | queued | retrieving | generating | done | retrieved (search only) | catalog | stopped | error
   scene: six header-style states for the pipeline scene (stages 5 and 6 are the chat's own)
   checks: vector, fragments, route, answer, cited → todo | run | done */
export function journey(turn) {
  const scene = Array(6).fill('idle'), checks = {vector: 'todo', fragments: 'todo', route: 'todo', answer: 'todo', cited: 'todo'};
  if (!turn) { scene[4] = scene[5] = 'rel'; return {phase: 'empty', step: 5, scene, checks, facts: {}}; }
  const metrics = turn.metrics || {}, sources = turn.sources || [], mode = metrics.routing_mode || '', model = turn.model || metrics.model || '';
  const retrieved = sources.length > 0 || metrics.vector_search_seconds != null;
  const catalog = mode === 'catalog', searchOnly = mode === 'search_only', running = live(turn.status);
  const phase = turn.status === 'queued' ? 'queued'
    : turn.status === 'running' ? (retrieved && !searchOnly ? 'generating' : 'retrieving')
    : turn.status === 'done' ? (catalog ? 'catalog' : searchOnly ? 'retrieved' : 'done')
    : turn.status === 'cancelled' ? 'stopped' : 'error';

  if (phase === 'queued') scene[4] = scene[5] = 'rel';
  else if (phase === 'retrieving') scene[4] = 'run';
  else if (phase === 'generating') { scene[4] = 'done'; scene[5] = 'run'; }
  else if (phase === 'done') scene[4] = scene[5] = 'done';
  else if (phase === 'retrieved') scene[4] = 'done';
  else if (phase === 'catalog') scene[0] = 'done';                    // answered from the library register, no model
  else if (phase === 'stopped') scene[4] = retrieved ? 'done' : 'idle';
  else { scene[4] = retrieved ? 'done' : 'error'; if (retrieved) scene[5] = 'error'; }

  const cited = citedCount(turn.answer, sources);
  if (!catalog) checks.vector = retrieved ? 'done' : turn.status === 'running' ? 'run' : 'todo';
  checks.fragments = sources.length ? 'done' : 'todo';
  checks.route = mode || model ? 'done' : running && retrieved ? 'run' : 'todo';
  checks.answer = turn.status === 'done' && turn.answer && !searchOnly ? 'done' : phase === 'generating' ? 'run' : 'todo';
  checks.cited = !running && cited ? 'done' : 'todo';

  const best = sources.reduce((top, source) => Math.max(top, relevance(source)), 0);
  return {phase, checks, scene,
    step: catalog ? 1 : ['generating', 'done'].includes(phase) || (['stopped', 'error'].includes(phase) && retrieved) ? 6 : 5,
    facts: {fragments: sources.length, documents: groupSources(sources).length, best, mode, model, cited, searchOnly, catalog,
      similarity: sources.some(source => source.match_type !== 'documented_relationship'),
      visual: metrics.visual_mode === 'images', cloud: Boolean(metrics.cloud) || mode.startsWith('external'),
      images: metrics.images_used, tokens: metrics.output_tokens, speed: metrics.generation_tok_s, reason: metrics.routing_reason || ''}};
}
