// No browser or canvas dependency: assert the layout and recorded drawing calls.
// Run with: node --test tests/kinklist_export.test.cjs
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '..');
const catalog = JSON.parse(fs.readFileSync(path.join(root, 'static/kinklist/catalog.json'))).categories;

function fixture() {
  const document = {
    createElement(tag) {
      assert.equal(tag, 'canvas');
      const canvas = { width: 0, height: 0, text: [], shapes: [] };
      const ctx = {
        canvas, textAlign: 'left', font: '18px Arial',
        fillRect() {}, beginPath() {}, fill() {}, moveTo() {}, bezierCurveTo() {}, stroke() {},
        roundRect(x, y, w, h) { canvas.shapes.push({ x, y, w, h }); },
        measureText(text) { return { width: [...text].length * Number(this.font.match(/([\d.]+)px/)[1]) * .55 }; },
        fillText(text, x, y) {
          assert.ok(x >= 0 && x <= canvas.width, `Text x: ${text}`);
          assert.ok(y >= 0 && y <= canvas.height, `Text y: ${text}`);
          canvas.text.push({ text: String(text), x, y });
        },
      };
      canvas.getContext = () => ctx;
      return canvas;
    },
  };
  const context = vm.createContext({ document });
  const source = fs.readFileSync(path.join(root, 'static/kinklist.js'), 'utf8');
  vm.runInContext(source.replace('  init();', `
    globalThis.api = {
      validate, makeCards,
      setState(value) { state = value; },
      setCatalog(value) { catalog = value; categoryId = value[0].id; }
    };`), context);
  context.api.setCatalog(catalog);
  return context.api;
}
function initial() {
  return { version: 1, name: '', theme: 'rose', custom: [], answers: {}, hideUnanswered: true, layout: 'sheet' };
}
function answer(state, cat) {
  for (const item of cat.items) for (const field of cat.fields) state.answers[`${item.id}:${field}`] = 'like';
}

test('partial collections use balanced whole panels and a short canvas', () => {
  const api = fixture(), state = initial();
  answer(state, catalog[0]); answer(state, catalog[1]);
  state.answers['touch-08:Receiving'] = 'like';
  api.setState(api.validate(state));
  const pages = api.makeCards();
  assert.equal(pages.length, 1);
  assert.ok(pages[0].height < 1100, 'Partial list should not become a poster');
  const titles = pages[0].text.filter((entry) => catalog.slice(0, 3).some((cat) => cat.name === entry.text));
  assert.equal(titles.length, 3, 'No duplicated category heading');
  assert.ok(!pages[0].text.some((entry) => /CHAPTER \d/.test(entry.text)), 'No numbered chapters on the card');
  assert.ok(!pages[0].text.some((entry) => entry.text.includes('A few yeses')), 'No subtitle on the card');
  assert.equal(new Set(titles.map((entry) => entry.y)).size, 1, 'Chapters begin at the same height');
  assert.equal(new Set(titles.map((entry) => entry.x)).size, 3, 'Every column is used');
});

test('complete starter collection fits one balanced sheet with every item and role', () => {
  const api = fixture(), state = initial();
  catalog.forEach((cat) => answer(state, cat));
  api.setState(api.validate(state));
  const pages = api.makeCards();
  assert.equal(pages.length, 1);
  assert.ok(pages[0].height <= 2400);
  const text = pages.flatMap((page) => page.text.map((entry) => entry.text));
  for (const cat of catalog) for (const item of cat.items) assert.ok(text.includes(item.name), item.name);
  assert.equal(text.filter((text) => text === 'Like').length, Object.keys(state.answers).length);
});

test('oversized custom chapter wraps, paginates, and keeps content inside each image', () => {
  const api = fixture(), state = initial();
  state.layout = 'category'; state.hideUnanswered = false;
  state.custom = Array.from({ length: 100 }, (_, i) => ({
    id: `custom-${i}`, category: catalog[0].id,
    name: `Custom ${i} ${'a long personal preference '.repeat(2)}`,
  }));
  api.setState(api.validate(state));
  const pages = api.makeCards();
  assert.ok(pages.length > 1);
  assert.equal(api.makeCards({ preview: true }).length, 1);
  for (const page of pages) {
    assert.equal(page.width, 900);
    assert.ok(page.height <= 2400);
    for (const shape of page.shapes) assert.ok(shape.y + shape.h <= page.height, 'Shape clipped');
  }
  const text = pages.flatMap((page) => page.text.map((entry) => entry.text));
  for (let i = 0; i < 100; i++) assert.ok(text.some((line) => line.startsWith(`Custom ${i} `)), `Missing custom item ${i}`);
});

test('single-category and empty previews avoid unused column space', () => {
  const api = fixture(), state = initial();
  api.setState(api.validate(state));
  assert.equal(api.makeCards()[0].width, 900);
  assert.ok(api.makeCards()[0].height < 800);
  answer(state, catalog[0]);
  api.setState(api.validate(state));
  assert.equal(api.makeCards()[0].width, 900);
  assert.ok(api.makeCards()[0].height < 1100);
});
