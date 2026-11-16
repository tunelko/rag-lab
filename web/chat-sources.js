// The chat lists sources by document, not by fragment. Pure helpers, no DOM.
export const relevance = source => Math.max(0, Math.min(1, source.relevance ?? (1 - Number(source.cosine_distance || 0))));

// The index stores no title. Use the document's own first heading when the opening
// fragment was retrieved; otherwise a readable form of the file name.
export function documentTitle(file, fragments = []) {
  for (const fragment of fragments) {
    const heading = Number(fragment.chunk) === 1 && String(fragment.text || '').match(/^\s*#\s+(\S.{1,118})$/m);
    if (heading) return heading[1].trim();
  }
  const name = String(file).split(/[\\/]/).pop()
    .replace(/\.[a-z0-9]{1,8}$/i, '')
    .replace(/_[a-z0-9]+_[0-9a-f]{6,}$/i, '')   // export suffixes such as _recipe_10c8e7
    .replace(/[_-][0-9a-f]{8,}$/i, '');
  const words = name.replace(/[-_]+/g, ' ').replace(/\s+/g, ' ').trim();
  return words ? words[0].toLocaleUpperCase() + words.slice(1) : String(file);
}

// One entry per document with the citations that point to it, most relevant first.
export function groupSources(sources = []) {
  const groups = new Map();
  for (const source of sources) {
    let group = groups.get(source.file);
    if (!group) { group = {file: source.file, fragments: [], citations: [], relevance: 0}; groups.set(source.file, group); }
    group.fragments.push(source); group.citations.push(Number(source.citation));
    group.relevance = Math.max(group.relevance, relevance(source));
  }
  return [...groups.values()]
    .map(group => ({...group, title: documentTitle(group.file, group.fragments)}))
    .sort((a, b) => b.relevance - a.relevance || a.citations[0] - b.citations[0]);
}

// Where a cited fragment sits among the fragments retrieved from its document.
export function fragmentPosition(sources = [], citation) {
  const source = sources.find(item => Number(item.citation) === Number(citation));
  if (!source) return null;
  const siblings = sources.filter(item => item.file === source.file);
  return {source, index: siblings.indexOf(source) + 1, total: siblings.length};
}
