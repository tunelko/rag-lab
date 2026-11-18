// A notice as a modal window: what a change of configuration means, or a warning the user has to see. It waits for
// the user (button, Escape), then gives the focus back. Notices that arrive while one is open wait their turn.
import {$, icon} from './ui.js';

const dialog = $('notice-dialog'), pending = [];
let returnFocus = null, run = null;

function show({title, text, tone = 'info', action = null}) {
  returnFocus = document.activeElement;
  dialog.dataset.tone = tone;
  $('notice-icon').replaceChildren(icon(tone === 'error' || tone === 'warn' ? 'alert' : 'info'));
  $('notice-title').textContent = title; $('notice-text').textContent = text;
  const button = $('notice-action'); button.hidden = !action; button.textContent = action?.label || ''; run = action?.run || null;
  dialog.showModal(); $('notice-ok').focus({preventScroll: true});
}

export function notice(details) {
  if (!dialog.open) { show(details); return; }
  // The same notice is not shown twice in a row.
  const same = item => item.title === details.title && item.text === details.text;
  if (!(same({title: $('notice-title').textContent, text: $('notice-text').textContent}) || pending.some(same))) pending.push(details);
}

$('notice-ok').addEventListener('click', () => dialog.close());
$('notice-action').addEventListener('click', () => { const action = run; dialog.close(); action?.(); });
dialog.addEventListener('close', () => {
  run = null;
  if (pending.length) { show(pending.shift()); return; }
  if (returnFocus?.isConnected && returnFocus.getClientRects().length) returnFocus.focus({preventScroll: true});
});
