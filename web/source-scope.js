// Readiness of the explicitly selected source; an empty source never expands.
export function sourceScope(status, topicId) {
  const topics=status?.library?.topics||[],documents=status?.documents||[];
  const topic=topics.find(item=>item.id===topicId);
  const exists=!topicId||topicId==='unassigned'||Boolean(topic);
  const ids=new Set([topicId,...topics.filter(item=>item.parent_id===topicId).map(item=>item.id)]);
  const members=documents.filter(item=>!topicId||(topicId==='unassigned'?!item.topic_id:ids.has(item.topic_id)));
  const indexed=members.filter(item=>item.indexed).length;
  return {exists,documents:members.length,indexed,
    elsewhere:documents.filter(item=>item.indexed).length>indexed};
}
