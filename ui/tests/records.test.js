import assert from 'node:assert/strict';
import test from 'node:test';
import { adopt, changes, confusedNotes, snapshot } from '../src/records.js';

const state = () => ({
  workspaceId: 'w1', activeChatId: 'c1', branchId: null,
  workspaces: [{ id: 'w1', name: '운영체제' }],
  chats: [{ id: 'c1', workspaceId: 'w1', messages: [] }],
  notes: [], tasks: [{ id: 't1', workspaceId: 'w1', text: '읽기', done: false }], memories: [], cards: [], edges: [],
});

test('only changed and removed records are sent', () => {
  const before = state();
  const saved = snapshot(before);
  assert.deepEqual(changes(saved, before), { upserts: [], deletes: [], next: saved });
  const after = { ...before, tasks: [{ ...before.tasks[0], done: true }], chats: [], concepts: [{ id: 'k1', workspaceId: 'w1', name: '교착' }] };
  const { upserts, deletes, next } = changes(saved, after);
  assert.deepEqual(upserts.map(item => `${item.kind}/${item.body.id}`), ['tasks/t1', 'concepts/k1']);
  assert.deepEqual(deletes, [{ kind: 'chats', id: 'c1' }]);
  assert.deepEqual(changes(next, after).upserts, []);
});

test('a first run sends everything this browser holds', () => {
  assert.equal(changes(new Map(), state()).upserts.length, 3);
});

test('server records replace local ones and keep a valid place', () => {
  const records = { workspaces: [{ id: 'w2', name: '서버 공간' }], chats: [{ id: 'c9', workspaceId: 'w2', messages: [] }], notes: [], tasks: [], memories: [], cards: [], edges: [], concepts: [] };
  const adopted = adopt(state(), records);
  assert.equal(adopted.workspaceId, 'w2');
  assert.equal(adopted.activeChatId, 'c9');
  assert.deepEqual(adopted.tasks, []);
  assert.equal(adopt({ ...state(), workspaceId: 'w2', activeChatId: 'c9' }, records).activeChatId, 'c9');
});

test('notes still marked confusing become context, understood ones do not', () => {
  const chats = [{ messages: [
    { role: 'user', content: 'TTFT가 왜\n길어지나요' },
    { role: 'assistant', sources: [{ page: 2 }, { page: 5 }], pdfNotes: { 1: { memo: 'a' }, 2: { memo: 'b', status: 'understood' } } },
  ] }];
  assert.equal(confusedNotes(chats), '아직 헷갈려하는 곳: 2쪽 TTFT가 왜 길어지나요');
  assert.equal(confusedNotes([{ messages: [] }]), '');
});
