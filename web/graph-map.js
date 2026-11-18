// The canvas of the Graph view, as in the design: entities are cards coloured by their type, relations are
// lines with their label at the midpoint, on the isometric grid. Each passage is drawn as a small star.
// It only draws what the server already accepted; the evidence panel shows what is selected.
import {$, node} from './ui.js';
import {t} from './i18n.js';
import {collectionUrl} from './collection-scope.js';
import {layoutGraph, placeStars, kindTones, rowMatches} from './graph-layout.js';

const label = (key, values = {}) => t(`graph.${key}`, values), NS = 'http://www.w3.org/2000/svg';
const short = (text, max = 22) => String(text).length > max ? String(text).slice(0, max - 1) + '…' : String(text);
// Text is monospace, so the width of a card follows from its text. Sizes are pixels at the normal zoom.
const NAME = 7.85, TYPE = 6.4, EDGE = 6.7, HEIGHT = 460;

function svg(tag, attributes = {}, text) {
  const item = document.createElementNS(NS, tag);
  for (const [key, value] of Object.entries(attributes)) item.setAttribute(key, String(value));
  if (text != null) item.textContent = text;
  return item;
}
// Where the segment between two centres meets the border of the card at `to`, with a small gap for the arrow.
function border(from, to, half) {
  const dx = to.x - from.x, dy = to.y - from.y;
  if (!dx && !dy) return {x: to.x, y: to.y};
  const scale = Math.min(dx ? (half.w + 6) / Math.abs(dx) : Infinity, dy ? (half.h + 6) / Math.abs(dy) : Infinity, .5);
  return {x: to.x - dx * scale, y: to.y - dy * scale};
}

export class GraphMap {
  constructor() {
    this.rows = []; this.box = $('graph-map'); this.selection = null;
    $('graph-search').addEventListener('input', () => this.filter());
    $('graph-map-source').addEventListener('change', () => this.filter());
    $('graph-zoom-in').addEventListener('click', () => this.zoom(.8));
    $('graph-zoom-out').addEventListener('click', () => this.zoom(1.25));
    $('graph-map-reset').addEventListener('click', () => this.home());
    this.box.addEventListener('keydown', event => { if (event.key === 'Escape' && this.selection) { event.preventDefault(); this.deselect(); } });
    this.hint();
  }

  render(rows) {
    this.rows = rows;
    const files = [...new Set(rows.filter(row => row.review === 'accepted' && row.verification === 'supported').map(row => row.file))].sort();
    const signature = JSON.stringify(files), picker = $('graph-map-source');
    if (this.sourceSignature !== signature) {
      const selected = picker.value;
      picker.replaceChildren(node('option', '', label('all_sources')), ...files.map(file => { const option = node('option', '', short(file.split('/').at(-1), 55)); option.value = file; option.title = file; return option; }));
      picker.firstElementChild.value = '';
      if (files.includes(selected)) picker.value = selected;
      this.sourceSignature = signature;
    }
    this.data = layoutGraph(rows);
    this.box.replaceChildren();
    this.positions = new Map(this.data.nodes.map(item => [item.id, item]));
    if (!this.data.nodes.length) {
      // Same canvas, with the reason it is empty and the action that fills it.
      const empty = node('div', 'graph-empty'), action = node('button', 'btn btn--primary', label('process'));
      action.type = 'button'; action.addEventListener('click', () => $('graph-extract').click());
      empty.append(node('p', '', label('map_empty')), action); this.box.append(empty);
      $('graph-map-count').textContent = label('visible_connections', {count: 0});
      this.selection = null; this.hint(); return;
    }
    Object.assign(this.data, placeStars(this.data.groups, this.data.edges));
    const tones = kindTones(this.data.nodes);
    for (const item of this.data.nodes) {
      item.name = short(item.label); item.type = short(item.kind, 24).toLocaleUpperCase();
      item.half = {w: Math.max(36, Math.max(item.name.length * NAME, item.type.length * TYPE) / 2 + 13), h: item.type ? 22 : 16};
      item.tone = tones.get(item.kind.toLocaleLowerCase());
    }
    const drawing = svg('svg', {role: 'group', 'aria-label': label('map_label'), preserveAspectRatio: 'xMidYMid meet'});
    this.drawing = drawing;
    const defs = svg('defs'), marker = svg('marker', {id: 'graph-arrow', viewBox: '0 0 10 10', refX: 9, refY: 5, markerWidth: 7, markerHeight: 7, orient: 'auto-start-reverse'});
    marker.append(svg('path', {d: 'M 0 0 L 10 5 L 0 10 z', class: 'graph-arrow'})); defs.append(marker); drawing.append(defs);
    for (const group of this.data.groups) {
      const caption = svg('text', {x: group.x, y: group.y - group.ry - 44, 'text-anchor': 'middle', class: 'graph-group-caption'}, short(group.file.split('/').at(-1), 46));
      caption.append(svg('title', {}, group.file)); drawing.append(caption);
    }
    // Two relations between the same pair of cards run side by side, each with its own label.
    const pairs = new Map();
    for (const edge of this.data.edges) { const key = JSON.stringify([edge.source, edge.target].sort()); if (!pairs.has(key)) pairs.set(key, []); pairs.get(key).push(edge); }
    for (const list of pairs.values()) list.forEach((edge, index) => { edge.shift = (index - (list.length - 1) / 2) * 26 * (edge.source < edge.target ? 1 : -1); });
    // Lines under the cards, their labels above them.
    this.lines = []; const tags = svg('g', {class: 'graph-tags'});
    for (const edge of this.data.edges) {
      const text = short((edge.row.polarity === 'negative' ? '¬ ' : '') + edge.row.predicate, 24), width = text.length * EDGE + 16;
      const control = svg('g', {class: 'gedge' + (edge.row.basis === 'inferred' ? ' is-inferred' : ''), tabindex: 0, role: 'button', 'aria-pressed': 'false',
        'aria-label': `${edge.row.subject_label} ${edge.row.predicate} ${edge.row.object_label}`});
      const line = svg('line', {'marker-end': 'url(#graph-arrow)'}), hit = svg('line', {class: 'graph-edge-hit'});
      const tag = svg('g', {class: 'gedge__tag'}); tag.append(svg('rect', {x: -width / 2, y: -10, width, height: 20, rx: 6}), svg('text', {'text-anchor': 'middle', y: 4}, text), svg('title', {}, edge.row.predicate));
      control.append(hit, line, svg('title', {}, edge.row.predicate)); tags.append(tag);
      const open = () => this.select({edge: edge.id});
      control.addEventListener('click', open); tag.addEventListener('click', open);
      control.addEventListener('focus', () => tag.classList.add('is-near')); control.addEventListener('blur', () => tag.classList.remove('is-near'));
      control.addEventListener('keydown', event => { if (['Enter', ' '].includes(event.key)) { event.preventDefault(); open(); } });
      drawing.append(control); this.lines.push({edge, control, line, hit, tag, width});
    }
    // Entities: a card with the type in small capitals over the name, in the pastel of its type.
    this.cards = [];
    for (const item of this.data.nodes) {
      const control = svg('g', {class: 'gnode' + (item.tone == null ? '' : ' gnode--' + item.tone), tabindex: 0, role: 'button', 'aria-pressed': 'false', 'aria-label': item.label + (item.kind ? ' · ' + item.kind : '')});
      const {w, h} = item.half;
      control.append(svg('rect', {class: 'gnode__shade', x: -w, y: -h + 3, width: 2 * w, height: 2 * h, rx: 12}), svg('rect', {class: 'gnode__card', x: -w, y: -h, width: 2 * w, height: 2 * h, rx: 12}));
      if (item.type) control.append(svg('text', {class: 'gnode__type', x: -w + 13, y: -5}, item.type));
      control.append(svg('text', {class: 'gnode__name', x: -w + 13, y: item.type ? 12 : 5}, item.name), svg('title', {}, item.label + (item.kind ? ' · ' + item.kind : '')));
      const open = () => this.select({node: item.id});
      control.addEventListener('keydown', event => { if (['Enter', ' '].includes(event.key)) { event.preventDefault(); open(); } });
      control.addEventListener('pointerdown', event => {
        event.stopPropagation(); const initial = this.point(event); let moved = false; control.setPointerCapture(event.pointerId);
        const move = next => { const point = this.point(next); item.x += point.x - initial.x; item.y += point.y - initial.y; initial.x = point.x; initial.y = point.y; moved = true; this.paint(); };
        const up = () => { control.removeEventListener('pointermove', move); control.removeEventListener('pointerup', up); control.removeEventListener('pointercancel', up); if (!moved) open(); };
        control.addEventListener('pointermove', move); control.addEventListener('pointerup', up); control.addEventListener('pointercancel', up);
      });
      drawing.append(control); this.cards.push({item, control});
    }
    // The wheel zooms around the pointer; a pinch on a touchpad arrives as the same event, in smaller steps.
    drawing.addEventListener('wheel', event => {
      event.preventDefault();
      const delta = event.deltaY * (event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? 400 : 1);
      this.zoom(Math.max(.8, Math.min(1.25, Math.exp(delta * .0015))), this.point(event));
    }, {passive: false});
    drawing.addEventListener('pointerdown', event => {
      if (event.target !== drawing) return;
      const initial = {x: event.clientX, y: event.clientY}, view = [...this.view]; drawing.setPointerCapture(event.pointerId);
      const move = next => { const rect = drawing.getBoundingClientRect(); this.view = [view[0] - (next.clientX - initial.x) * view[2] / rect.width, view[1] - (next.clientY - initial.y) * view[3] / rect.height, view[2], view[3]]; this.applyView(); };
      const up = () => { drawing.removeEventListener('pointermove', move); drawing.removeEventListener('pointerup', up); drawing.removeEventListener('pointercancel', up); };
      drawing.addEventListener('pointermove', move); drawing.addEventListener('pointerup', up); drawing.addEventListener('pointercancel', up);
    });
    drawing.append(tags); this.box.append(drawing);
    // One pastel per type of entity, the most frequent first; the legend is always in view.
    if (tones.size) {
      const legend = node('ul', 'graph-legend'); legend.setAttribute('aria-label', label('legend'));
      for (const [kind, tone] of tones) legend.append(node('li', 'pill pill--sm gtone--' + tone, kind));
      this.box.append(legend);
    }
    this.home(); this.paint(); this.filter();
    // A refresh keeps what was selected, if it is still there.
    const kept = this.selection;
    this.links = [];
    if (kept?.edge != null && this.data.edges.some(edge => edge.id === kept.edge)) this.select(kept);
    else if (kept?.node != null && this.positions.has(kept.node)) this.select(kept);
    else { this.selection = null; this.hint(); }
  }

  // The same entity mentioned in two passages: a dashed bridge between its two cards, decided by the model and assessed.
  bridges(data) {
    this.drawing?.querySelector('.graph-bridges')?.remove(); this.links = [];
    if (!this.drawing || !data?.identity_links?.length) return;
    const key = entity => JSON.stringify([entity.unit, entity.kind || '', entity.label]), byId = new Map(data.entities.map(entity => [entity.id, entity]));
    const layer = svg('g', {class: 'graph-bridges'}), drawn = new Set();
    for (const link of data.identity_links) {
      const left = byId.get(link.left_entity), right = byId.get(link.right_entity), a = left && this.positions.get(key(left)), b = right && this.positions.get(key(right));
      // Mentions that share a card need no bridge, and two cards get one bridge however many links join them.
      const pair = a && b && JSON.stringify([a.id, b.id].sort());
      if (!a || !b || a === b || drawn.has(pair)) continue;
      drawn.add(pair);
      const control = svg('g', {class: 'gbridge', tabindex: 0, role: 'button', 'aria-pressed': 'false', 'aria-label': label('same_entity') + ': ' + left.label});
      const path = svg('path'), hit = svg('path', {class: 'graph-edge-hit'}); control.append(hit, path, svg('title', {}, label('same_entity')));
      const open = () => this.select({bridge: link.id});
      control.addEventListener('click', open); control.addEventListener('keydown', event => { if (['Enter', ' '].includes(event.key)) { event.preventDefault(); open(); } });
      layer.append(control); this.links.push({link, left, right, a, b, control, path, hit});
    }
    // Above the relations, under the cards.
    this.drawing.insertBefore(layer, this.cards[0]?.control || null); this.paint();
  }

  // Search and source filter dim what does not match instead of removing it.
  filter() {
    if (!this.data?.nodes.length) return;
    const term = $('graph-search').value, source = $('graph-map-source').value, bright = new Set();
    let count = 0;
    for (const {edge, control, tag} of this.lines) {
      const match = rowMatches(edge.row, term, source);
      control.classList.toggle('is-dim', !match); tag.classList.toggle('is-dim', !match);
      if (match) { count++; bright.add(edge.source); bright.add(edge.target); }
    }
    for (const {item, control} of this.cards) control.classList.toggle('is-dim', !bright.has(item.id));
    $('graph-map-count').textContent = label('visible_connections', {count});
  }

  point(event) { const point = this.drawing.createSVGPoint(); point.x = event.clientX; point.y = event.clientY; return point.matrixTransform(this.drawing.getScreenCTM().inverse()); }

  paint() {
    for (const {item, control} of this.cards) control.setAttribute('transform', `translate(${item.x} ${item.y})`);
    for (const {edge, line, hit, tag, width} of this.lines) {
      const a = this.positions.get(edge.source), b = this.positions.get(edge.target), from = border(b, a, a.half), to = border(a, b, b.half);
      const dx = to.x - from.x, dy = to.y - from.y, length = Math.hypot(dx, dy) || 1, nx = -dy / length, ny = dx / length;
      if (edge.shift) { from.x += nx * edge.shift; from.y += ny * edge.shift; to.x += nx * edge.shift; to.y += ny * edge.shift; }
      for (const [key, value] of Object.entries({x1: from.x, y1: from.y, x2: to.x, y2: to.y})) { line.setAttribute(key, value); hit.setAttribute(key, value); }
      // The label sits at the middle of the visible stretch, between the two cards. On a stretch shorter than the label
      // it steps aside (upwards, or outwards between parallel relations), so the line and its arrow stay in view.
      let x = (from.x + to.x) / 2, y = (from.y + to.y) / 2;
      if (length < width + 34) { const side = edge.shift ? Math.sign(edge.shift) : ny > 0 ? -1 : 1, reach = Math.abs(nx) * width / 2 + Math.abs(ny) * 10 + 6; x += nx * side * reach; y += ny * side * reach; }
      tag.setAttribute('transform', `translate(${x} ${y})`);
    }
    // A bridge arcs over the map between the two cards of one entity.
    for (const {a, b, path, hit} of this.links || []) { const d = `M${a.x} ${a.y - a.half.h} Q${(a.x + b.x) / 2} ${Math.min(a.y, b.y) - 150} ${b.x} ${b.y - b.half.h}`; path.setAttribute('d', d); hit.setAttribute('d', d); }
  }

  // Normal zoom is one pixel per unit; a map smaller than the canvas is centred, a larger one starts at its first passage.
  home() {
    if (!this.drawing) return;
    const width = this.box.clientWidth || 960, fit = Math.max(1, this.data.width / width, this.data.height / HEIGHT);
    // A map a little larger than the canvas is shown whole; a much larger one starts at its first passage, at normal size.
    this.view = fit <= 1.6 ? [(this.data.width - width * fit) / 2, (this.data.height - HEIGHT * fit) / 2, width * fit, HEIGHT * fit]
      : [0, 0, width, HEIGHT];
    this.applyView();
  }
  // Zooms keeping one point of the map where it is on screen: the centre for the buttons, the pointer for the wheel.
  zoom(factor, at = null) {
    if (!this.drawing) return;
    const [x, y, width, height] = this.view, next = width * factor; if (next < 240 || next > 8000) return;
    const point = at || {x: x + width / 2, y: y + height / 2};
    this.view = [point.x - (point.x - x) * factor, point.y - (point.y - y) * factor, next, height * factor]; this.applyView();
  }
  applyView() { this.drawing.setAttribute('viewBox', this.view.join(' ')); }

  select(selection) {
    this.selection = selection;
    for (const {edge, control, tag} of this.lines) { const on = selection.edge === edge.id; control.classList.toggle('is-selected', on); tag.classList.toggle('is-selected', on); control.setAttribute('aria-pressed', String(on)); }
    for (const {item, control} of this.cards) { const on = selection.node === item.id; control.classList.toggle('is-selected', on); control.setAttribute('aria-pressed', String(on)); }
    for (const item of this.links || []) { const on = selection.bridge === item.link.id; item.control.classList.toggle('is-selected', on); item.control.setAttribute('aria-pressed', String(on)); }
    if (selection.edge != null) this.show(this.data.edges.find(edge => edge.id === selection.edge).row);
    else if (selection.bridge != null) this.same((this.links || []).find(item => item.link.id === selection.bridge));
    else this.neighbors(this.positions.get(selection.node));
  }
  deselect() {
    this.selection = null;
    for (const {control, tag} of [...this.lines, ...this.cards, ...(this.links || [])]) { control.classList.remove('is-selected'); tag?.classList.remove('is-selected'); control.setAttribute('aria-pressed', 'false'); }
    this.hint();
  }

  heading(pane, eyebrow, pill, tone) {
    const top = node('div', 'journey__top'); top.append(node('span', 'eyebrow', eyebrow));
    if (pill) top.append(node('span', 'pill pill--sm' + (tone ? ' pill--' + tone : ''), pill));
    pane.append(top);
  }
  hint() {
    const pane = $('graph-map-detail'); pane.replaceChildren();
    this.heading(pane, label('evidence'));
    pane.append(node('h3', 'graph-evidence__title', label('explore')), node('p', 'meta', label('explore_hint')));
  }
  // Why two mentions are one entity, and where each of them comes from.
  same(item) {
    const pane = $('graph-map-detail'); pane.replaceChildren();
    if (!item) return this.hint();
    this.heading(pane, label('same_entity'), label('accepted'), 'mint');
    pane.append(node('h3', 'graph-evidence__title', item.left.label === item.right.label ? item.left.label : `${item.left.label} = ${item.right.label}`));
    if (item.link.reason) pane.append(node('blockquote', 'evidence-quote', item.link.reason));
    const facts = node('dl', 'graph-evidence__facts'), add = (key, value) => facts.append(node('dt', '', key), node('dd', '', value));
    add(label('document'), item.left.file); add(label('document'), item.right.file);
    if (item.link.model) add(label('decided_by'), [item.link.provider, item.link.model].filter(Boolean).join(' · '));
    pane.append(facts);
    if (item.link.verification_reason) pane.append(node('p', 'meta', item.link.verification_reason));
    pane.append(node('p', 'meta', label('other_hint')));
  }
  neighbors(item) {
    const pane = $('graph-map-detail'); pane.replaceChildren();
    this.heading(pane, label('entity'), item.kind);
    pane.append(node('h3', 'graph-evidence__title', item.label));
    const list = node('div', 'graph-evidence__links');
    for (const edge of this.data.edges.filter(edge => [edge.source, edge.target].includes(item.id))) {
      const button = node('button', 'btn btn--secondary btn--sm', `${edge.row.subject_label} → ${edge.row.predicate} → ${edge.row.object_label}`);
      button.type = 'button'; button.addEventListener('click', () => this.select({edge: edge.id})); list.append(button);
    }
    pane.append(list, node('p', 'meta', label('other_hint')));
  }
  show(row) {
    const pane = $('graph-map-detail'); pane.replaceChildren();
    this.heading(pane, label('evidence'), label(row.review), row.review === 'accepted' ? 'mint' : '');
    pane.append(node('h3', 'graph-evidence__title', `${row.subject_label} → ${row.predicate} → ${row.object_label}`), node('blockquote', 'evidence-quote', row.quote));
    const facts = node('dl', 'graph-evidence__facts'), add = (key, value, className = '') => facts.append(node('dt', '', key), node('dd', className, value));
    add(label('document'), row.file);
    // The server may number the passage within its document; until then the page is what is known.
    if (row.passage_number && row.passage_total) add(label('passage'), label('passage_of', {number: row.passage_number, total: row.passage_total}));
    else if (row.page) add(label('page'), row.page);
    add(label('assessment'), label('verification_' + row.verification), row.verification === 'supported' ? 'is-ok' : '');
    pane.append(facts);
    if (row.verification_note) pane.append(node('p', 'meta', row.verification_note));
    const actions = node('div', 'graph-evidence__actions'), open = node('button', 'btn btn--secondary btn--sm', label('open_document')), ask = node('button', 'btn btn--ghost btn--sm', label('ask_chat'));
    open.type = ask.type = 'button';
    open.addEventListener('click', () => window.dispatchEvent(new CustomEvent('rag:document', {detail: {url: collectionUrl('/api/graph/source?unit=' + row.unit), file: row.file, page: row.page || 1, trigger: open}})));
    // The question is written in the chat, not sent: the user decides.
    ask.addEventListener('click', () => {
      window.dispatchEvent(new CustomEvent('rag:navigate', {detail: '/'}));
      const question = $('question'); question.value = label('ask_question', {subject: row.subject_label, predicate: row.predicate, object: row.object_label, file: row.file.split('/').at(-1)});
      question.dispatchEvent(new Event('input', {bubbles: true})); question.focus();
    });
    actions.append(open, ask); pane.append(actions, node('p', 'meta', label('other_hint')));
  }
}
