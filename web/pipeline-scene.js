// The isometric pipeline scene: one drawing (the template in index.html) reused by every view.
// It only shows what its owner passes: which of the six stages are on, which one is working, and their values.
import {translateStatic} from './i18n.js';

const STAGES = 6;
// Header states → scene: "rel" (the view acts on the stage) and "done" light the platform, "run" also beats.
export function sceneState(state) {
  return {on: ['rel', 'done', 'run'].includes(state), run: state === 'run', error: state === 'error'};
}

export class PipelineScene {
  constructor(host, {mini = false} = {}) {
    const view = document.getElementById('pipeline-scene-template').content.cloneNode(true);
    if (mini) view.querySelector('.scene__steps').remove();
    translateStatic(view);
    this.stations = [...view.querySelectorAll('.station')];
    this.steps = [...view.querySelectorAll('.scene__steps li')];
    host.classList.add('scene');
    host.classList.toggle('scene--mini', mini);
    host.replaceChildren(view);
    this.host = host;
    window.addEventListener('rag:languagechange', () => translateStatic(host));
  }

  // states: six of idle | rel | done | run | error. values: six short texts (optional).
  set(states, values = []) {
    for (let index = 0; index < STAGES; index++) {
      const state = sceneState(states[index]);
      for (const element of [this.stations[index], this.steps[index]]) {
        if (!element) continue;
        element.classList.toggle('is-on', state.on);
        element.classList.toggle('is-run', state.run);
        element.classList.toggle('is-error', state.error);
      }
      const value = this.steps[index]?.querySelector('.scene__value');
      if (value && values[index] != null) { value.textContent = values[index]; value.title = values[index]; }
    }
    this.host.classList.toggle('is-working', states.includes('run'));
  }
}
