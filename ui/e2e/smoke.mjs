// Headless walk through the screens with any text PDF:
// upload + live processing log, cited answer, highlight from cited rects, margin note (S4) and its
// 헷갈려요 / 이해했어요 state, region question (S5), abstention (S3), annotated PDF (S8), canvas, concepts,
// notes, journal, tasks, then: records come back from the server in a fresh browser, and a new
// workspace starts with no documents.
// usage: BASE=http://127.0.0.1:8000 PDF=<file> Q='<a question the PDF answers>' node e2e/smoke.mjs
import { chromium } from 'playwright';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';

const BASE = process.env.BASE ?? 'http://127.0.0.1:8000';
const { PDF, Q } = process.env;
if (!PDF || !Q) throw new Error('set PDF and Q');
const here = p => fileURLToPath(new URL(p, import.meta.url));
const shot = (page, name) => page.screenshot({ path: here(`../shots/smoke-${name}.png`) });
const step = message => console.log('✓', message);

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2, acceptDownloads: true });
const errors = [];
page.on('pageerror', error => errors.push(error.message));
page.on('console', message => message.type() === 'error' && errors.push(message.text()));
page.on('request', request => { if (new URL(request.url()).origin !== new URL(BASE).origin && !request.url().startsWith('blob:') && !request.url().startsWith('data:')) errors.push(`left the origin: ${request.url()}`); });

const nav = label => page.locator('.sidebar nav .nav-item', { hasText: label }).first().click();
const answers = () => page.locator('.chat-pane:not(.side-pane) .message.assistant');
async function ask(question) {
  const before = await answers().count();
  const box = page.locator('.chat-pane:not(.side-pane) .composer textarea');
  await box.fill(question);
  await box.press('Enter');
  await page.locator('.chat-pane:not(.side-pane) .send-button').waitFor({ timeout: 180000 });
  const after = await answers().count();
  if (after !== before + 1) throw new Error(`no answer: ${await page.locator('.inline-error').innerText().catch(() => '')}`);
  return answers().nth(before);
}
async function openDocument() {
  await nav('문서');
  await page.locator('.document-name').first().click();
  await page.locator('.document-reader .textLayer span, .document-reader canvas').first().waitFor({ timeout: 60000 });
}

try {
  await page.goto(BASE);
  await nav('문서');
  await page.locator('input[type=file]').setInputFiles(PDF);
  const status = page.locator('.document-index-status').first();
  await status.waitFor({ timeout: 60000 });
  const seen = new Set();
  for (let i = 0; i < 600 && !(await status.innerText()).includes('검색 가능'); i += 1) { seen.add(await status.innerText()); await page.waitForTimeout(300); }
  if (!(await status.innerText()).includes('검색 가능')) throw new Error(`not indexed: ${await status.innerText()}`);
  step(`uploaded + indexed (log lines seen: ${[...seen].join(' | ').slice(0, 160)})`);

  await openDocument();
  await page.getByRole('button', { name: '문서에 질문' }).click();
  let answer = await ask(Q);
  if (await answer.locator('.answer-flag').count()) throw new Error('the question was withheld');
  const steps = await answer.locator('.answer-progress li').count();
  if (steps < 3) throw new Error(`progress has ${steps} steps`);
  step(`cited answer, ${steps} progress steps kept`);
  await answer.locator('.answer-source-link').first().click();
  await page.locator('.pdf-highlight').first().waitFor({ state: 'visible', timeout: 30000 });
  const boxes = await page.locator('.pdf-highlight').count();
  await page.waitForTimeout(800);
  await shot(page, '1-answer-highlight');
  step(`citation click -> highlight (${boxes} boxes)`);

  await answer.locator('.answer-source-note[aria-pressed="true"]').waitFor({ timeout: 30000 });
  await page.getByRole('button', { name: /^필기 \d+$/ }).click();
  await page.locator('.reader-notes li button').first().click();
  const memo = await page.locator('.reader-note textarea').first().inputValue();
  if (!memo.trim()) throw new Error('the margin note is empty');
  await shot(page, '2-note');
  step(`margin note written by the backend: “${memo.slice(0, 40)}”`);
  const state = page.locator('.reader-note button.note-status').first();
  if (!(await state.innerText()).includes('헷갈려요')) throw new Error('a new note should start as 헷갈려요');
  await state.click();
  await page.locator('.reader-note button.note-status.understood').first().waitFor();
  step('note marked 이해했어요');

  const models = await page.locator('.composer .model-picker').count();
  if (!models) throw new Error('no model picker');
  step('model picker present');

  // S5: drag an area on the page and ask about it
  await page.getByRole('button', { name: '표 또는 그림 선택' }).click();
  const picker = page.locator('.pdf-region-picker');
  await picker.waitFor();
  const box = await picker.boundingBox();
  // the page is taller than the reader, so drag inside the part of it that is on screen
  const top = Math.max(box.y, 480) + 20;
  await page.mouse.move(box.x + box.width * 0.15, top);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width * 0.85, Math.min(top + 200, box.y + box.height - 5), { steps: 8 });
  await page.mouse.up();
  await page.getByRole('button', { name: '이 영역 질문하기' }).click();
  answer = await ask('이 영역은 무엇을 말하나요?');
  await shot(page, '3-region');
  step(`region question answered${await answer.locator('.answer-flag.abstained').count() ? ' (withheld)' : ''}`);

  // S3: a question the document cannot answer is held back
  await openDocument();
  await page.getByRole('button', { name: '문서에 질문' }).click();
  answer = await ask('화성의 대기압은 몇 파스칼인가요?');
  await answer.locator('.answer-flag.abstained').waitFor({ timeout: 5000 });
  await shot(page, '4-abstain');
  step('unsupported question -> 보류');

  // S8
  const download = page.waitForEvent('download', { timeout: 60000 });
  await page.getByRole('button', { name: '필기 PDF 받기' }).click();
  const file = await (await download).path();
  const bytes = await readFile(file);
  if (bytes.subarray(0, 4).toString() !== '%PDF' || !bytes.includes('/Highlight')) throw new Error('annotated PDF has no highlight annotation');
  step(`annotated PDF downloaded (${bytes.length} bytes, highlight annotation inside)`);

  await nav('캔버스');
  await page.locator('.canvas-board').waitFor();
  const cards = await page.locator('.canvas-board > *').count();
  await shot(page, '5-canvas');
  step(`canvas opens (${cards} elements)`);

  await nav('개념 정리');
  await page.getByRole('button', { name: '개념 추가' }).click();
  await page.locator('.concept-add input').first().fill('통신비밀보호법');
  await page.locator('.concept-add').getByRole('button', { name: '추가' }).click();
  await page.locator('.concept-entry', { hasText: '통신비밀보호법' }).first().waitFor();
  await shot(page, '6-concepts');
  step('concept added, understanding level shown');

  for (const label of ['노트', '일지', '할 일']) { await nav(label); await page.locator('.page, .notes-layout, main').first().waitFor(); }
  await page.locator('input[aria-label="새 할 일"]').fill('3쪽 다시 읽기');
  await page.keyboard.press('Enter');
  await page.locator('.task-row', { hasText: '3쪽 다시 읽기' }).waitFor();
  step('notes, journal, tasks open; task added');

  // the records are on the server: a browser that has never been here shows the same work
  await page.waitForTimeout(1500);
  const other = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const second = await other.newPage();
  await second.goto(BASE);
  await second.locator('.sidebar .recent-item').first().waitFor({ timeout: 15000 });
  const chats = await second.locator('.sidebar .recent-item').count();
  await second.locator('.sidebar nav .nav-item', { hasText: '할 일' }).first().click();
  await second.locator('.task-row', { hasText: '3쪽 다시 읽기' }).waitFor({ timeout: 10000 });
  await second.locator('.sidebar nav .nav-item', { hasText: '문서' }).first().click();
  await second.locator('.document-name').first().click();
  await second.getByRole('button', { name: /^필기 \d+$/ }).click();
  await second.locator('.reader-notes li button').first().click();
  await second.locator('.reader-note button.note-status.understood').first().waitFor({ timeout: 10000 });
  step(`fresh browser: ${chats} chats, the task and the note state came back from the server`);

  // documents belong to their workspace
  await second.getByRole('button', { name: '새 작업 공간' }).click();
  await second.locator('.modal input').first().fill('다른 과목');
  await second.getByRole('button', { name: '작업 공간 만들기' }).click();
  await second.locator('.sidebar nav .nav-item', { hasText: '문서' }).first().click();
  await second.locator('.upload-area').waitFor();
  await second.waitForTimeout(800);
  if (await second.locator('.document-row').count()) throw new Error('a new workspace should have no documents');
  step('new workspace starts with no documents');
  await other.close();

  if (errors.length) throw new Error(`page errors: ${errors.slice(0, 5).join(' | ')}`);
  console.log('PASS');
} catch (error) {
  await shot(page, 'failure');
  console.error('FAIL', error.message, errors.slice(0, 5));
  process.exitCode = 1;
} finally {
  await browser.close();
}
