// The learner's records live on the server. The screens load them once, then send only the
// items that changed: each item of each collection is one record, compared by its JSON.
export const collections = ['workspaces', 'chats', 'notes', 'tasks', 'memories', 'cards', 'edges', 'concepts'];

export function snapshot(state) {
  const saved = new Map();
  for (const kind of collections) for (const item of state[kind] || []) saved.set(`${kind}/${item.id}`, JSON.stringify(item));
  return saved;
}

// What to send so the server matches `state`, given what it already holds (`saved`).
export function changes(saved, state) {
  const next = snapshot(state);
  const upserts = [];
  for (const kind of collections) for (const item of state[kind] || []) {
    if (saved.get(`${kind}/${item.id}`) !== next.get(`${kind}/${item.id}`)) upserts.push({ kind, body: item });
  }
  const deletes = [...saved.keys()].filter(key => !next.has(key)).map(key => {
    const cut = key.indexOf('/');
    return { kind: key.slice(0, cut), id: key.slice(cut + 1) };
  });
  return { upserts, deletes, next };
}

// Replace what this browser holds with the server's records, keeping where the learner was.
export function adopt(state, records) {
  const workspaceId = records.workspaces.some(item => item.id === state.workspaceId) ? state.workspaceId : records.workspaces[0].id;
  const open = id => records.chats.some(chat => chat.id === id && chat.workspaceId === workspaceId) ? id : null;
  const activeChatId = open(state.activeChatId) ?? records.chats.findLast(chat => chat.workspaceId === workspaceId)?.id ?? null;
  return { ...state, ...Object.fromEntries(collections.map(kind => [kind, records[kind] || []])), workspaceId, activeChatId, branchId: open(state.branchId) };
}

// Margin notes the learner still marks 헷갈려요, as lines for the next question's context.
export function confusedNotes(chats) {
  const lines = chats.flatMap(chat => chat.messages.flatMap((answer, index) => Object.entries(answer.pdfNotes || {})
    .filter(([, note]) => note.status !== 'understood')
    .map(([number]) => ({ page: answer.sources?.[number - 1]?.page, question: chat.messages[index - 1]?.content }))
    .filter(item => item.page && item.question)
    .map(item => `${item.page}쪽 ${item.question.replace(/\s+/g, ' ').slice(0, 80)}`)));
  return lines.length ? `아직 헷갈려하는 곳: ${lines.slice(-5).join('; ')}` : '';
}
