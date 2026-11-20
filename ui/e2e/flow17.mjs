// The 17 steps of the earlier demo walk-through, one to one, on these screens.
// Every step is timed and recorded as pass / fail / n-a; a failed step does not stop the run.
// usage: BASE=http://127.0.0.1:8000 PDF=<file> OUT=<result.json> node e2e/flow17.mjs
// Assumes an empty store. Questions fit the e-commerce issue paper used for the rehearsal.
import { chromium } from 'playwright';
import { writeFile, mkdir } from 'node:fs/promises';

const BASE = process.env.BASE ?? 'http://127.0.0.1:8000';
const { PDF, OUT = 'flow17.json', SHOTS = 'shots' } = process.env;
if (!PDF) throw new Error('set PDF');
const Q = {
  first: '한국의 이커머스 시장은 지난 20년간 어떤 성장세를 보이고 있으며, 이를 통해 소비자들에게 무엇을 제공하고 있나요?',
  firstPage: 4, firstFact: '20%',
  pickPage: 3, pick: '체리슈머',
  selection: '이게 무슨 뜻이에요?',
  term: '체리슈머는 뭐예요?',
  followUp: '그럼 그건 이커머스 시장에 어떤 영향을 줘요?',
  outside: '퓨리오사AI의 2025년 매출은 얼마인가요?',
  dangling: '그럼 그건 물가가 오르면 어떻게 돼요?',
  memory: '용어는 쉬운 말로 풀어서 설명해 주세요.',
};
await mkdir(SHOTS, { recursive: true });

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2 });
const errors = [];
page.on('pageerror', error => errors.push(error.message));
page.on('console', message => message.type() === 'error' && errors.push(message.text()));
const chatBodies = [];
page.on('request', request => { if (request.url().endsWith('/api/chat') && request.method() === 'POST') chatBodies.push(request.postDataJSON()); });

const results = [];
async function run(n, name, body) {
  const started = Date.now();
  let status = 'pass', detail = '';
  try { detail = (await body()) || ''; if (detail.startsWith('n/a')) status = 'n/a'; if (detail.startsWith('partial')) status = 'partial'; }
  catch (error) { status = 'fail'; detail = error.message.split('\n')[0]; await page.screenshot({ path: `${SHOTS}/fail-${n}.png` }).catch(() => {}); }
  const seconds = +((Date.now() - started) / 1000).toFixed(1);
  results.push({ n, name, status, seconds, detail });
  console.log(`${status === 'pass' ? '✓' : status === 'fail' ? '✗' : '–'} ${n}. ${name} [${seconds}s] ${detail}`);
}
const shot = name => page.screenshot({ path: `${SHOTS}/${name}.png` });
const nav = label => page.locator('.sidebar nav .nav-item', { hasText: label }).first().click();
const pane = '.chat-pane:not(.side-pane)';
const answers = () => page.locator(`${pane} .message.assistant`);
async function ask(question) {
  const before = await answers().count();
  const box = page.locator(`${pane} .composer textarea`);
  await box.fill(question);
  await box.press('Enter');
  await page.waitForTimeout(300);
  await page.locator(`${pane} .send-button`).waitFor({ timeout: 180000 });
  if (await answers().count() !== before + 1) throw new Error(`no answer: ${await page.locator('.inline-error').innerText().catch(() => '')}`);
  return answers().nth(before);
}
const withheld = answer => answer.locator('.answer-flag.abstained').count();
const records = async () => (await page.request.get(`${BASE}/api/records`)).json();
async function lastAnswerRecord(question) {
  await page.waitForTimeout(1500);
  const chats = (await records()).chats;
  for (const chat of chats.toReversed()) {
    const index = chat.messages.findLastIndex(message => message.role === 'user' && message.content === question);
    if (index >= 0) return { chat, message: chat.messages[index + 1] };
  }
  throw new Error('the answer is not in the saved records');
}
async function gotoPdfPage(number) {
  const input = page.getByLabel('이동할 페이지');
  await input.fill(String(number));
  await input.press('Enter');
  await page.locator(`.pdf-sheet[aria-label="${number}쪽 본문"] .textLayer span`).first().waitFor({ timeout: 30000 });
}

let first, firstRecord, selectionAnswer;

await run(1, 'uploaded + indexed + opened', async () => {
  await page.goto(BASE);
  await page.getByRole('button', { name: '새 작업 공간' }).click();
  await page.locator('.modal input').first().fill('소비자와 시장 복습');
  await page.getByRole('button', { name: '작업 공간 만들기' }).click();
  await nav('문서');
  await page.locator('input[type=file]').setInputFiles(PDF);
  const status = page.locator('.document-index-status').first();
  await status.waitFor({ timeout: 60000 });
  const seen = new Set();
  for (let i = 0; i < 600 && !(await status.innerText()).includes('검색 가능'); i += 1) { seen.add(await status.innerText()); await page.waitForTimeout(250); }
  if (!(await status.innerText()).includes('검색 가능')) throw new Error(`not indexed: ${await status.innerText()}`);
  await page.locator('.document-name').first().click();
  await page.locator('.document-reader .textLayer span').first().waitFor({ timeout: 60000 });
  await shot('01-opened');
  return `log lines: ${[...seen].join(' | ').slice(0, 140)}`;
});

await run(2, 'cited answer', async () => {
  await page.getByRole('button', { name: '문서에 질문' }).click();
  first = await ask(Q.first);
  if (await withheld(first)) throw new Error('the question was withheld');
  firstRecord = await lastAnswerRecord(Q.first);
  const text = firstRecord.message.content;
  const cited = [...text.matchAll(/\[(\d+)\]/g)].map(match => +match[1]);
  if (!cited.length) throw new Error('the answer has no citation number');
  const pages = [...new Set(cited.map(number => firstRecord.message.sources[number - 1]?.page))];
  if (!pages.includes(Q.firstPage)) throw new Error(`cited pages ${pages} do not include page ${Q.firstPage}`);
  const fake = text.includes('(가짜');
  if (!fake && !text.includes(Q.firstFact)) throw new Error(`the answer does not state ${Q.firstFact}`);
  return `cited pages ${pages.join(',')}; ${fake ? 'fake generation: answer text not checked' : `states ${Q.firstFact}`}; server ${firstRecord.message.measured?.latencyS ?? '?'}s`;
});

await run(3, 'processing log kept with the answer', async () => {
  const steps = await first.locator('.answer-progress li').count();
  if (steps < 3) throw new Error(`processing log has ${steps} steps`);
  return `${steps} steps`;
});

await run(4, 'note chip under the answer', async () => {
  await first.locator('.answer-source-note[aria-pressed="true"]').first().waitFor({ timeout: 30000 });
  const notes = Object.values(firstRecord.message.pdfNotes || (await lastAnswerRecord(Q.first)).message.pdfNotes || {});
  if (!notes.length || !notes[0].memo.trim()) throw new Error('the note has no text');
  return `note: “${notes[0].memo.slice(0, 50)}”`;
});

await run(5, 'citation click -> highlight', async () => {
  const link = first.locator('.answer-source-link').first();
  const [, number, pageNumber] = (await link.innerText()).match(/\[(\d+)\].*?(\d+)(?:–\d+)?쪽/);
  await link.click();
  const sheet = page.locator(`.pdf-sheet[aria-label="${pageNumber}쪽 본문"]`);
  await sheet.waitFor({ timeout: 30000 });
  const marks = sheet.locator(`.pdf-highlight[data-highlight="citation-${number}"]`);
  await marks.first().waitFor({ state: 'visible', timeout: 30000 });
  await page.waitForTimeout(800);
  await shot('05-highlight');
  const source = firstRecord.message.sources[number - 1];
  if (source.page !== +pageNumber) throw new Error(`link says page ${pageNumber}, source is page ${source.page}`);
  const frame = await sheet.boundingBox();
  const boxes = await marks.evaluateAll(nodes => nodes.map(node => { const r = node.getBoundingClientRect(); return [r.left, r.top, r.right, r.bottom]; }));
  const top = Math.min(...boxes.map(b => b[1])), bottom = Math.max(...boxes.map(b => b[3]));
  const shown = [(top - frame.y) / frame.height, (bottom - frame.y) / frame.height];
  if (!source.rects?.length) return `partial: highlight on page ${pageNumber} (${boxes.length} boxes) but the source carries no rects to compare`;
  const want = [Math.min(...source.rects.map(r => r[1])), Math.max(...source.rects.map(r => r[3]))];
  if (Math.abs(shown[0] - want[0]) > 0.03 || Math.abs(shown[1] - want[1]) > 0.03) throw new Error(`highlight at y ${shown.map(v => v.toFixed(3))}, source rects at y ${want.map(v => v.toFixed(3))}`);
  return `page ${pageNumber}, ${boxes.length} boxes, y ${shown.map(v => v.toFixed(3)).join('–')} matches the source rects ${want.map(v => v.toFixed(3)).join('–')}`;
});

await run(6, 'selection -> quote in composer', async () => {
  await gotoPdfPage(Q.pickPage);
  const span = page.locator(`.pdf-sheet[aria-label="${Q.pickPage}쪽 본문"] .textLayer span`, { hasText: Q.pick }).first();
  await span.waitFor({ timeout: 20000 });
  await span.evaluate(node => {
    const range = document.createRange();
    range.selectNodeContents(node);
    const selection = window.getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
  });
  await page.getByRole('button', { name: '이 부분 질문하기' }).click();
  const quote = await page.locator('.composer-quote').first().innerText();
  if (!quote.includes(`${Q.pickPage}쪽`) || !quote.includes(Q.pick)) throw new Error(`quote reads “${quote}”`);
  return `quote: ${quote.slice(0, 80)}`;
});

await run(7, 'selection answer', async () => {
  selectionAnswer = await ask(Q.selection);
  const body = chatBodies.at(-1);
  if (!JSON.stringify(body).includes(Q.pick)) throw new Error('the selected text was not sent with the question');
  if (await withheld(selectionAnswer)) throw new Error('the selection question was withheld');
  const { message } = await lastAnswerRecord(Q.selection);
  return `answered with ${message.sources?.length ?? 0} sources; server ${message.measured?.latencyS ?? '?'}s`;
});

await run(8, 'margin note pin -> card', async () => {
  const { message } = await lastAnswerRecord(Q.selection);
  const [number] = Object.keys(message.pdfNotes || {});
  if (!number) throw new Error('the selection answer left no note');
  const notePage = message.sources[number - 1].page;
  await selectionAnswer.locator('.answer-source-link').first().click();
  await gotoPdfPage(notePage);
  const pin = page.locator(`.pdf-sheet[aria-label="${notePage}쪽 본문"] .pdf-note-marker`).first();
  await pin.waitFor({ timeout: 30000 });
  await pin.click();
  const memo = await page.locator('.reader-note textarea').first().inputValue();
  if (!memo.trim()) throw new Error('the note card is empty');
  await shot('08-note-card');
  return `pin on page ${notePage}; card: “${memo.slice(0, 50)}”`;
});

await run(9, 'term question answered', async () => {
  const answer = await ask(Q.term);
  if (await withheld(answer)) throw new Error('withheld');
  const { message } = await lastAnswerRecord(Q.term);
  if (!message.content.includes('(가짜') && !/혜택|알뜰|실속/.test(message.content)) throw new Error('the answer does not describe the term');
  return `server ${message.measured?.latencyS ?? '?'}s`;
});

await run(10, 'follow-up answered with a citation', async () => {
  const answer = await ask(Q.followUp);
  if (await withheld(answer)) throw new Error('follow-up withheld: chat context not used');
  await answer.locator('.answer-source-link').first().waitFor({ timeout: 5000 });
  const { message } = await lastAnswerRecord(Q.followUp);
  await shot('10-follow-up');
  return `sources on pages ${[...new Set(message.sources.map(source => source.page))].join(',')}; server ${message.measured?.latencyS ?? '?'}s`;
});

await run(11, 'abstained', async () => {
  const answer = await ask(Q.outside);
  await answer.locator('.answer-flag.abstained').waitFor({ timeout: 5000 });
  const { message } = await lastAnswerRecord(Q.outside);
  if (!message.abstained) throw new Error('saved record is not marked abstained');
  await shot('11-abstained');
  return `gate ${message.measured?.gate}, top score ${message.measured?.topScore?.toFixed?.(4)}; server ${message.measured?.latencyS ?? '?'}s`;
});

let noteRows = 0, confusedRows = 0;
await run(12, 'notes list -> jump to note', async () => {
  const toggle = page.getByRole('button', { name: /^필기 \d+$/ });
  if (!(await page.locator('.reader-notes').count())) await toggle.click();
  const rows = page.locator('.reader-notes > ul > li > button');
  await rows.first().waitFor();
  noteRows = await rows.count();
  confusedRows = await page.locator('.reader-notes > ul > li > button .note-status.confused').count();
  const row = rows.first();
  const rowPage = +(await row.locator('span').first().innerText()).replace('쪽', '');
  if ((await row.getAttribute('aria-expanded')) !== 'true') await row.click();
  await page.locator(`.pdf-sheet[aria-label="${rowPage}쪽 본문"] .pdf-highlight`).first().waitFor({ state: 'visible', timeout: 30000 });
  await shot('12-note-list');
  return `${noteRows} notes; first row opened page ${rowPage} with its highlight`;
});

await run(13, 'note filter (헷갈려요)', async () => {
  if (!noteRows) throw new Error('no notes listed');
  const filter = await page.locator('.reader-notes > button, .reader-notes [role=tab]', { hasText: '헷갈려요' }).count();
  if (!filter) return `n/a: no filter button on these screens; each row carries a 헷갈려요 badge instead (${confusedRows} of ${noteRows} marked)`;
  return `${confusedRows} confused of ${noteRows}`;
});

await run(14, 'note -> its answer in the chat', async () => {
  const card = page.locator('.reader-note').first();
  await card.waitFor();
  await card.getByRole('button', { name: '대화에서 보기' }).click();
  await page.waitForTimeout(800);
  const noteChats = (await records()).chats.filter(chat => chat.messages.some(message => message.pdfNotes && Object.keys(message.pdfNotes).length));
  const ids = noteChats.flatMap(chat => chat.messages.filter(message => message.pdfNotes && Object.keys(message.pdfNotes).length).map(message => message.id));
  const visible = await page.evaluate(list => list.filter(id => {
    const node = document.querySelector(`[data-message-id="${id}"]`);
    if (!node) return false;
    const r = node.getBoundingClientRect();
    return r.bottom > 0 && r.top < window.innerHeight;
  }), ids);
  if (!visible.length) throw new Error('the answer that owns the note is not on screen');
  await shot('14-note-to-answer');
  return 'the owning answer is on screen (no focus ring on these screens)';
});

await run(15, 'memory saved; confused spots listed', async () => {
  await page.locator(`${pane} .message.assistant`).first().getByRole('button', { name: '기억하기' }).click();
  await page.locator('.app-topbar').getByRole('button', { name: '기억', exact: true }).click();
  await page.locator('.memory-card').first().waitFor({ timeout: 5000 });
  const area = page.locator('.memory-card textarea').first();
  await area.fill(Q.memory);
  await page.waitForTimeout(1500);
  const saved = (await records()).memories;
  if (!saved.some(memory => memory.content === Q.memory)) throw new Error('the memory is not in the saved records');
  await shot('15-memory');
  const listed = await page.locator('.memory-card', { hasText: '헷갈' }).count();
  const sent = chatBodies.at(-1)?.context || '';
  return `partial: memory saved on the server; confused spots are not listed on the memory page (${listed} shown) — they are sent with each question instead (context of the last question ${sent.includes('헷갈') || sent.length ? `${sent.length} chars` : 'empty'})`;
});

await run(16, 'new chat starts clean', async () => {
  await page.getByRole('button', { name: '새 대화' }).click();
  const answer = await ask(Q.dangling);
  const body = chatBodies.at(-1);
  const turns = body.messages.filter(message => message.role === 'user').length;
  if (turns !== 1) throw new Error(`the new chat sent ${turns} user turns`);
  if (!body.context.includes(Q.memory)) throw new Error('the saved memory was not sent with the new chat');
  return `one user turn sent, memory + confused notes sent as context (${body.context.length} chars); ${await withheld(answer) ? 'withheld' : 'answered'}`;
});

await run(17, 'switched back to the first chat', async () => {
  const items = page.locator('.sidebar .recent-item');
  const count = await items.count();
  if (count < 2) throw new Error(`expected 2+ chats in the sidebar, got ${count}`);
  await page.locator('.sidebar .recent-item', { hasText: Q.first.slice(0, 12) }).first().click();
  await answers().first().waitFor();
  const shown = await answers().count();
  if (shown !== 1) throw new Error(`the first chat shows ${shown} answers, expected 1`);
  await shot('17-chats');
  return `${count} chats in the sidebar; the first chat shows its ${shown} answer`;
});

const real = errors.filter(error => !/favicon/.test(error));
await writeFile(OUT, JSON.stringify({ base: BASE, pdf: PDF.split('/').pop(), at: new Date().toISOString(), results, consoleErrors: real }, null, 2));
const failed = results.filter(result => result.status === 'fail').length;
console.log(`${results.filter(result => result.status === 'pass').length} pass, ${results.filter(result => result.status === 'partial').length} partial, ${results.filter(result => result.status === 'n/a').length} n/a, ${failed} fail; console errors: ${real.length}`);
if (real.length) console.log(real.slice(0, 5));
await browser.close();
process.exitCode = failed ? 1 : 0;
