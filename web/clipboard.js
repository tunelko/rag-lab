// Copies text to the clipboard. The Clipboard API only exists on a secure origin (HTTPS or this computer itself), and
// the application is often opened over plain HTTP from another device of the local network; there, the text is
// copied through a selection, which works on any origin while it answers a press of the user.
export async function writeClipboard(text) {
  const value = String(text ?? '');
  if (window.isSecureContext && navigator.clipboard?.writeText) {
    try { await navigator.clipboard.writeText(value); return true; } catch { /* try the selection below */ }
  }
  const area = document.createElement('textarea'), active = document.activeElement;
  area.value = value; area.readOnly = true; area.tabIndex = -1; area.setAttribute('aria-hidden', 'true');
  Object.assign(area.style, {position: 'fixed', top: '0', left: '0', width: '1px', height: '1px', padding: '0', border: '0', opacity: '0'});
  // With a modal window open the rest of the page cannot take a selection: the text goes inside that window.
  const open = [...document.querySelectorAll('dialog[open]')].at(-1);
  (open || document.body).append(area);
  let copied = false;
  try { area.select(); area.setSelectionRange(0, value.length); copied = document.execCommand('copy'); } catch { copied = false; }
  area.remove();
  if (active?.isConnected) active.focus({preventScroll: true});
  return copied;
}
