// What kind of document the viewer is showing, and the front matter of a Markdown file. Pure, so it is unit-tested.

// The server previews a file as text, as a PDF page or as an image; a text file with a Markdown name is read as Markdown.
export function documentKind(file = '', kind = 'text') {
  if (kind === 'pdf' || kind === 'image') return kind;
  return /\.(md|markdown)$/i.test(String(file)) ? 'markdown' : 'text';
}

export const KIND_LABEL = {markdown: 'MD', text: 'TXT', pdf: 'PDF', image: 'IMG'};

// A Markdown file may open with a block of "key: value" lines between two "---" lines. Returns those pairs and the
// rest of the text; anything that does not look like such a block is left in the body untouched.
export function splitFrontMatter(text = '') {
  const source = String(text), match = /^﻿?---[ \t]*\r?\n([\s\S]*?)\r?\n---[ \t]*(?:\r?\n|$)/.exec(source);
  if (!match) return {meta: [], body: source};
  // Some exporters write the block as one JSON object instead of "key: value" lines.
  if (match[1].trim().startsWith('{')) {
    try {
      const data = JSON.parse(match[1]);
      if (data && typeof data === 'object' && !Array.isArray(data)) {
        const show = value => value == null ? '' : Array.isArray(value) ? value.map(show).join(', ') : typeof value === 'object' ? JSON.stringify(value) : String(value);
        const pairs = Object.entries(data).map(([key, value]) => [key, show(value)]);
        if (pairs.length) return {meta: pairs, body: source.slice(match[0].length)};
      }
    } catch { /* not JSON: read it as lines below */ }
  }
  const meta = [];
  for (const line of match[1].split(/\r?\n/)) {
    if (!line.trim()) continue;
    const pair = /^([A-Za-z0-9_.\- ]{1,60}):\s*(.*)$/.exec(line);
    if (pair) { meta.push([pair[1].trim(), pair[2].trim().replace(/^(["'])(.*)\1$/, '$2')]); continue; }
    // A continuation: an indented line or a list item under the last key.
    const more = /^\s+(?:-\s*)?(.*)$/.exec(line) || /^-\s+(.*)$/.exec(line);
    if (!more || !meta.length) return {meta: [], body: source};
    const last = meta[meta.length - 1], value = more[1].trim().replace(/^(["'])(.*)\1$/, '$2');
    last[1] = last[1] ? last[1] + ', ' + value : value;
  }
  return meta.length ? {meta, body: source.slice(match[0].length)} : {meta: [], body: source};
}
