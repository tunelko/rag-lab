// The isometric pipeline scene: one drawing (the template in index.html) reused by every view.
// It only shows what its owner passes: which of the six stages are on, which one is working, and their values.
import {t, translateStatic} from './i18n.js';

const SVG = 'http://www.w3.org/2000/svg', STAGES = 6;
const KEYS = ['ingestion', 'chunks', 'embeddings', 'vectors', 'retrieval', 'model'];
// Header states → scene: "rel" (the view acts on the stage) and "done" light the platform, "run" also beats.
export function sceneState(state) {
  return {on: ['rel', 'done', 'run'].includes(state), run: state === 'run', error: state === 'error'};
}

/* Diagonal layout: the six platforms stand along one isometric axis (2:1), so each stage sits in front of the
   previous one and the document order is also the painter's order. A platform is a 56-unit square; STEP leaves
   room between two of them for the arrow that lies on the floor. */
const STEP = {x: 92, y: 46}, ORIGIN = {x: 62, y: 50}, VIEW = '0 0 584 328';
export function diagonalCentre(index) { return {x: ORIGIN.x + STEP.x * index, y: ORIGIN.y + STEP.y * index}; }
// Previous/next stage for the arrow keys; the ends wrap, Home and End jump.
export function nextStage(current, key) {
  if (key === 'Home') return 0;
  if (key === 'End') return STAGES - 1;
  const move = {ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1}[key];
  return move ? (current + move + STAGES) % STAGES : current;
}

function shape(tag, attributes = {}, text) {
  const element = document.createElementNS(SVG, tag);
  for (const [name, value] of Object.entries(attributes)) element.setAttribute(name, value);
  if (text != null) element.textContent = text;
  return element;
}
// Rewriting identical text on every poll would drop a selection.
const put = (element, text) => { if (element.textContent !== text) element.textContent = text; };
function make(tag, className = '', text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text != null) element.textContent = text;
  return element;
}

export class PipelineScene {
  /* mini: drawing only (chat side column).
     explain: {targets: {stage: {key, element | href}}} turns the scene into an explainer: diagonal drawing whose
     platforms can be pressed, a tab per stage and a panel with what the stage does, its value now and where it is set. */
  constructor(host, {mini = false, explain = null} = {}) {
    const view = document.getElementById('pipeline-scene-template').content.cloneNode(true);
    this.host = host; this.explain = explain; this.selected = null;
    this.states = Array(STAGES).fill('idle'); this.values = Array(STAGES).fill('—');
    this.stations = [...view.querySelectorAll('.station')];
    this.steps = explain ? [] : [...view.querySelectorAll('.scene__steps li')];
    if (mini || explain) view.querySelector('.scene__steps').remove();
    host.classList.add('scene');
    host.classList.toggle('scene--mini', mini);
    if (explain) this.build(view); else { translateStatic(view); host.replaceChildren(view); }
    window.addEventListener('rag:languagechange', () => { translateStatic(host); if (this.explain) this.render(); });
  }

  build(view) {
    const svg = view.querySelector('.iso'), id = this.host.id || 'scene';
    svg.setAttribute('viewBox', VIEW); svg.classList.add('iso--diagonal');
    const arrows = svg.querySelector('.arrows'), packets = svg.querySelector('.packets');
    arrows.replaceChildren(); packets.replaceChildren();
    this.stations.forEach((station, index) => {
      const centre = diagonalCentre(index);
      station.setAttribute('transform', `translate(${centre.x} ${centre.y})`);
      // The drawing moves on press and hover; the outer group keeps the place.
      const body = shape('g', {class: 'station__body'}); body.append(...station.childNodes);
      // Footprint of the platform: it shows as a sliver when the drawing lifts.
      station.append(shape('polygon', {class: 'station__shadow', points: '0,-18 56,10 0,38 -56,10'}));
      const tag = shape('g', {class: 'station__tag'}); tag.append(shape('circle', {cx: -40, cy: 31, r: 10}), shape('text', {x: -40, y: 35}, index + 1));
      station.append(body, tag);
      station.addEventListener('click', () => this.select(index, true));
      if (index < STAGES - 1) {
        // On the floor, from the middle of this platform's front edge to the next one's back edge.
        arrows.append(shape('path', {d: `M${centre.x + 33} ${centre.y + 16.5} ${centre.x + 60} ${centre.y + 30}m-4.1-7.6 4.1 7.6-8.5 1.4`}));
        packets.append(shape('circle', {class: 'packet', cx: centre.x + 33, cy: centre.y + 16.5, r: 4}));
      }
    });
    translateStatic(view);
    const stage = make('div', 'scene__stage'), panel = make('div', 'scene__panel'), hint = make('p', 'scene__hint');
    hint.dataset.i18n = 'scene.hint'; stage.append(view, hint); translateStatic(stage);
    this.tablist = make('div', 'scene__tabs'); this.tablist.setAttribute('role', 'tablist');
    this.tabs = KEYS.map((key, index) => {
      const tab = make('button', 'scene__tab'), name = make('span', 'scene__tab-name'), value = make('span', 'scene__tab-value num');
      tab.type = 'button'; tab.id = `${id}-tab-${index + 1}`; tab.setAttribute('role', 'tab'); tab.setAttribute('aria-controls', `${id}-detail`);
      name.append(make('span', 'step-no', index + 1), make('span')); tab.append(name, value);
      tab.addEventListener('click', () => this.select(index));
      tab.addEventListener('keydown', event => {
        const target = nextStage(index, event.key);
        if (target === index) return;
        event.preventDefault(); this.select(target); this.tabs[target].tab.focus();
      });
      this.tablist.append(tab);
      return {tab, label: name.lastChild, value};
    });
    const detail = make('div', 'scene__detail'), head = make('div', 'scene__detail-head');
    detail.id = `${id}-detail`; detail.setAttribute('role', 'tabpanel');
    this.detail = {box: detail, step: make('span', 'eyebrow num'), state: make('span', 'pill pill--sm'), title: make('h3', 'scene__detail-title'),
      text: make('p', 'scene__detail-text'), nowLabel: make('span', 'field-label'), now: make('span', 'scene__value num'), go: make('a', 'btn btn--sm'), note: make('p', 'meta')};
    const now = make('p', 'scene__now'); now.append(this.detail.nowLabel, this.detail.now);
    head.append(this.detail.step, this.detail.state);
    this.detail.go.addEventListener('click', event => {
      const target = this.explain.targets?.[this.selected + 1];
      if (!target?.element) return;
      event.preventDefault();
      const card = target.element();
      card.scrollIntoView({behavior: 'smooth', block: 'start'});
      card.setAttribute('tabindex', '-1'); card.focus({preventScroll: true});
      card.classList.remove('is-pointed'); void card.offsetWidth; card.classList.add('is-pointed');
    });
    detail.append(head, this.detail.title, this.detail.text, now, this.detail.go, this.detail.note);
    panel.append(this.tablist, detail);
    this.host.classList.add('scene--explain');
    this.host.replaceChildren(stage, panel);
    this.render();
  }

  // Pressing a platform or a tab chooses the stage; from the drawing the focus follows to its tab.
  select(index, fromDrawing = false) {
    this.selected = index; this.chosen = true; this.render();
    if (fromDrawing) this.tabs[index].tab.focus({preventScroll: true});
  }

  render() {
    // Until someone chooses, show the first stage this view acts on.
    if (!this.chosen) { const first = this.states.findIndex(state => sceneState(state).on); this.selected = first < 0 ? 0 : first; }
    const index = this.selected, key = KEYS[index], target = this.explain.targets?.[index + 1], state = sceneState(this.states[index]), d = this.detail;
    this.tablist.setAttribute('aria-label', t('scene.tabs_label'));
    this.tabs.forEach((item, position) => {
      const chosen = position === index;
      item.tab.setAttribute('aria-selected', String(chosen)); item.tab.tabIndex = chosen ? 0 : -1;
      put(item.label, t(`scene.${KEYS[position]}`));
      this.stations[position].classList.toggle('is-selected', chosen);
    });
    d.box.setAttribute('aria-labelledby', this.tabs[index].tab.id);
    put(d.step, t('journey.step', {step: index + 1}));
    d.state.hidden = !state.run && !state.error; put(d.state, t(state.error ? 'scene.state_error' : 'scene.state_run')); d.state.dataset.state = state.error ? 'error' : 'run';
    put(d.title, t(`scene.${key}`)); d.title.dataset.stage = String(index + 1);
    put(d.text, t(`scene.explain_${key}`));
    put(d.nowLabel, t('scene.now')); put(d.now, this.values[index]); d.now.title = this.values[index];
    d.go.hidden = !target; d.note.hidden = !target?.note;
    if (target) { put(d.go, t(target.key)); d.go.setAttribute('href', target.href || '#' + target.anchor); if (target.note) put(d.note, t(target.note)); }
  }

  // states: six of idle | rel | done | run | error. values: six short texts (optional).
  set(states, values = []) {
    for (let index = 0; index < STAGES; index++) {
      this.states[index] = states[index] || 'idle';
      if (values[index] != null) this.values[index] = values[index];
      const state = sceneState(states[index]);
      for (const element of [this.stations[index], this.steps[index], this.tabs?.[index].tab]) {
        if (!element) continue;
        element.classList.toggle('is-on', state.on);
        element.classList.toggle('is-run', state.run);
        element.classList.toggle('is-error', state.error);
      }
      const value = this.steps[index]?.querySelector('.scene__value') || this.tabs?.[index].value;
      if (value && values[index] != null && value.textContent !== values[index]) { value.textContent = values[index]; value.title = values[index]; }
    }
    this.host.classList.toggle('is-working', states.includes('run'));
    if (this.explain) this.render();
  }
}
