// The canvas of the Graph view, drawn in the same isometric language as the pipeline scene: every passage is an
// island on the floor, every entity a small cube coloured by its type, every relation a line on the floor.
// It only draws what the server already accepted; the evidence panel shows what is selected.
import {$, node} from './ui.js';
import {t} from './i18n.js';
import {collectionUrl} from './collection-scope.js';
import {layoutGraph, placeIslands, kindTones, rowMatches} from './graph-layout.js';

const label = (key, values = {}) => t(`graph.${key}`, values), NS = 'http://www.w3.org/2000/svg';
const short = (text, max = 15) => String(text).length > max ? String(text).slice(0, max - 1) + '…' : String(text);
// A cube of half-width A stands H above the floor; the label sits under it. Sizes are pixels at the normal zoom.
const A = 15, H = 14, DEPTH = 8, HEIGHT = 460;

function svg(tag, attributes = {}, text) {
  const item = document.createElementNS(NS, tag);
  for (const [key, value] of Object.entries(attributes)) item.setAttribute(key, String(value));
  if (text != null) item.textContent = text;
  return item;
}
const points = list => list.map(([x, y]) => `${x},${y}`).join(' ');

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
    const size = placeIslands(this.data.groups, {width: this.box.clientWidth || 960}), reach = size.side * size.cell;
    Object.assign(this.data, size);
    const tones = kindTones(this.data.nodes);
    const drawing = svg('svg', {role: 'group', 'aria-label': label('map_label'), preserveAspectRatio: 'xMidYMid meet'});
    this.drawing = drawing;
    const defs = svg('defs'), marker = svg('marker', {id: 'graph-arrow', viewBox: '0 0 10 10', refX: 9, refY: 5, markerWidth: 6, markerHeight: 6, orient: 'auto-start-reverse'});
    marker.append(svg('path', {d: 'M 0 0 L 10 5 L 0 10 z', class: 'graph-arrow'})); defs.append(marker); drawing.append(defs);
    // Floor first: one island per passage, far rows before near ones.
    for (const group of this.data.groups) {
      const {x, y} = group, island = svg('g', {class: 'gisland'});
      island.append(
        svg('polygon', {class: 'gisland__left', points: points([[x - reach, y + reach / 2], [x, y + reach], [x, y + reach + DEPTH], [x - reach, y + reach / 2 + DEPTH]])}),
        svg('polygon', {class: 'gisland__right', points: points([[x, y + reach], [x + reach, y + reach / 2], [x + reach, y + reach / 2 + DEPTH], [x, y + reach + DEPTH]])}),
        svg('polygon', {class: 'gisland__top', points: points([[x, y], [x + reach, y + reach / 2], [x, y + reach], [x - reach, y + reach / 2]])}));
      const caption = svg('text', {x, y: y + reach + DEPTH + 16, 'text-anchor': 'middle', class: 'graph-group-caption'}, short(group.file.split('/').at(-1), 26));
      caption.append(svg('title', {}, group.file)); island.append(caption); drawing.append(island);
    }
    // Relations lie on the floor, under the cubes; their labels float above everything.
    this.lines = []; const tags = svg('g', {class: 'graph-tags'});
    for (const edge of this.data.edges) {
      const text = short((edge.row.polarity === 'negative' ? '¬ ' : '') + edge.row.predicate, 26), width = text.length * 6.7 + 16;
      const control = svg('g', {class: 'gedge' + (edge.row.basis === 'inferred' ? ' is-inferred' : ''), tabindex: 0, role: 'button', 'aria-pressed': 'false',
        'aria-label': `${edge.row.subject_label} ${edge.row.predicate} ${edge.row.object_label}`});
      const line = svg('line', {'marker-end': 'url(#graph-arrow)'}), hit = svg('line', {class: 'graph-edge-hit'});
      const tag = svg('g', {class: 'gedge__tag'}); tag.append(svg('rect', {x: -width / 2, y: -10, width, height: 20, rx: 6}), svg('text', {'text-anchor': 'middle', y: 4}, text));
      control.append(hit, line, svg('title', {}, edge.row.predicate)); tags.append(tag);
      const open = () => this.select({edge: edge.id});
      control.addEventListener('click', open); tag.addEventListener('click', open);
      control.addEventListener('pointerenter', () => tag.classList.add('is-near')); control.addEventListener('pointerleave', () => tag.classList.remove('is-near'));
      control.addEventListener('focus', () => tag.classList.add('is-near')); control.addEventListener('blur', () => tag.classList.remove('is-near'));
      control.addEventListener('keydown', event => { if (['Enter', ' '].includes(event.key)) { event.preventDefault(); open(); } });
      drawing.append(control); this.lines.push({edge, control, line, hit, tag});
    }
    // Entities: a cube per entity, coloured by type; back to front so near cubes cover far ones.
    this.cards = [];
    for (const item of [...this.data.nodes].sort((a, b) => a.depth - b.depth)) {
      const tone = tones.get(item.kind.toLocaleLowerCase());
      const control = svg('g', {class: 'gnode' + (tone == null ? '' : ' gnode--' + tone), tabindex: 0, role: 'button', 'aria-pressed': 'false', 'aria-label': item.label + (item.kind ? ' · ' + item.kind : '')});
      const cube = svg('g', {class: 'gnode__cube'});
      cube.append(
        svg('polygon', {class: 'gnode__left', points: points([[-A, -H], [0, -H + A / 2], [0, A / 2], [-A, 0]])}),
        svg('polygon', {class: 'gnode__right', points: points([[0, -H + A / 2], [A, -H], [A, 0], [0, A / 2]])}),
        svg('polygon', {class: 'gnode__top', points: points([[0, -H - A / 2], [A, -H], [0, -H + A / 2], [-A, -H]])}));
      control.append(svg('ellipse', {class: 'gnode__shade', cx: 0, cy: A / 2 - 1, rx: A, ry: A / 2}), cube, svg('text', {class: 'gnode__name', 'text-anchor': 'middle', y: A / 2 + 15}, short(item.label)),
        svg('title', {}, item.label + (item.kind ? ' · ' + item.kind : '')));
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
    drawing.addEventListener('pointerdown', event => {
      if (event.target !== drawing && !event.target.closest('.gisland')) return;
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
    if (kept?.edge != null && this.data.edges.some(edge => edge.id === kept.edge)) this.select(kept);
    else if (kept?.node != null && this.positions.has(kept.node)) this.select(kept);
    else { this.selection = null; this.hint(); }
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
    for (const {edge, line, hit, tag} of this.lines) {
      const a = this.positions.get(edge.source), b = this.positions.get(edge.target), dx = b.x - a.x, dy = b.y - a.y, length = Math.hypot(dx, dy) || 1, trim = Math.min(A + 3, length / 3);
      const ends = {x1: a.x + dx / length * trim, y1: a.y + dy / length * trim, x2: b.x - dx / length * trim, y2: b.y - dy / length * trim};
      for (const [key, value] of Object.entries(ends)) { line.setAttribute(key, value); hit.setAttribute(key, value); }
      tag.setAttribute('transform', `translate(${(a.x + b.x) / 2} ${(a.y + b.y) / 2 - 12})`);
    }
  }

  // Normal zoom is one pixel per unit; a map smaller than the canvas is centred, a larger one starts at its first islands.
  home() {
    if (!this.drawing) return;
    const width = this.box.clientWidth || 960;
    this.view = [this.data.width < width ? (this.data.width - width) / 2 : 0, this.data.height < HEIGHT ? (this.data.height - HEIGHT) / 2 : 0, width, HEIGHT];
    this.applyView();
  }
  zoom(factor) { if (!this.drawing) return; const [x, y, width, height] = this.view, next = width * factor; if (next < 240 || next > 8000) return; this.view = [x + (width - next) / 2, y + (height - height * factor) / 2, next, height * factor]; this.applyView(); }
  applyView() { this.drawing.setAttribute('viewBox', this.view.join(' ')); }

  select(selection) {
    this.selection = selection;
    for (const {edge, control, tag} of this.lines) { const on = selection.edge === edge.id; control.classList.toggle('is-selected', on); tag.classList.toggle('is-selected', on); control.setAttribute('aria-pressed', String(on)); }
    for (const {item, control} of this.cards) { const on = selection.node === item.id; control.classList.toggle('is-selected', on); control.setAttribute('aria-pressed', String(on)); }
    if (selection.edge != null) this.show(this.data.edges.find(edge => edge.id === selection.edge).row);
    else this.neighbors(this.positions.get(selection.node));
  }
  deselect() {
    this.selection = null;
    for (const {control, tag} of [...this.lines, ...this.cards]) { control.classList.remove('is-selected'); tag?.classList.remove('is-selected'); control.setAttribute('aria-pressed', 'false'); }
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
