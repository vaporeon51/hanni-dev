// Run: deno test --allow-read tests/media_visibility_test.js
import assert from "node:assert/strict";
import vm from "node:vm";

function harness(view, responses) {
  const source = Deno.readTextFileSync(`static/${view}.js`);
  const start = source.indexOf("function createMedia(");
  const end = source.indexOf("\nfunction ", start + 1);
  let hidden = 0;
  let requests = 0;
  const timers = new Map();
  const elements = [];
  const context = vm.createContext({
    AbortController,
    MEDIA_RETRY_DELAYS_MS: [1500, 4000, 9000],
    document: { createElement(tag) {
      const element = {
        tagName: tag.toUpperCase(), style: {}, children: [],
        replaceChildren(...children) { this.children = children; },
        removeAttribute() {}, getAttribute() { return this.src; },
        getBoundingClientRect() { return { height: 100 }; },
        load() {}, pause() {}, play() { return Promise.resolve(); },
      };
      elements.push(element);
      return element;
    } },
    window: {
      setTimeout(fn) { const id = timers.size + 1; timers.set(id, fn); return id; },
      clearTimeout(id) { timers.delete(id); },
    },
    mediaWindowObserver: null, videoPlaybackObserver: null,
    showCollectionLink() {}, externalLink() { return {}; },
    fetch: async () => {
      requests++;
      const [status, payload = {}] = responses.shift();
      return { status, ok: status === 200, json: async () => payload,
        headers: { get() { return null; } }, body: { cancel: async () => {} } };
    },
    onUnavailable() { hidden++; },
  });
  vm.runInContext(source.slice(start, end), context);
  const args = view === "scroll" ? ", () => {}, onUnavailable" : ", onUnavailable";
  const controller = vm.runInContext(`createMedia({content_link_id: 42, url: "https://goyangi.pics/v/test.webp"}${args})`, context);
  return { controller, elements, timers, hidden: () => hidden, requests: () => requests,
    async start() {
      if (view === "scroll") await controller.load();
      else { controller.observe(); await new Promise(resolve => setTimeout(resolve, 0)); }
    } };
}

for (const view of ["app", "sets", "scroll"]) {
  Deno.test(`${view}: a page 404 hides once and disposal prevents reloading`, async () => {
    const h = harness(view, [[404]]);
    await h.start();
    assert.equal(h.hidden(), 1);
    await h.start();
    assert.equal(h.requests(), 1);
  });

  Deno.test(`${view}: asset 404 hides, transient asset failure retries`, async () => {
    for (const status of [404, 503]) {
      const h = harness(view, [[200, {kind: "image", url: "/api/feed/42/asset"}], [status]]);
      await h.start();
      await h.elements.find(element => element.tagName === "IMG").onerror();
      assert.equal(h.hidden(), status === 404 ? 1 : 0);
      assert.equal(h.timers.size, status === 404 ? 0 : 1);
      h.controller.dispose();
      assert.equal(h.timers.size, 0);
    }
  });

  Deno.test(`${view}: 503 remains retryable and a fresh visit can show recovered media`, async () => {
    const h = harness(view, [[503]]);
    await h.start();
    assert.equal(h.hidden(), 0);
    assert.equal(h.timers.size, 1);
    h.controller.dispose();
    const fresh = harness(view, [[200, {kind: "image", url: "/api/feed/42/asset"}]]);
    await fresh.start();
    assert.equal(fresh.hidden(), 0);
    assert.equal(fresh.elements.find(element => element.tagName === "IMG").src, "/api/feed/42/asset");
    fresh.controller.dispose();
  });
}

function getFunction(view, name, globals) {
  const source = Deno.readTextFileSync(`static/${view}.js`);
  const start = source.indexOf(`function ${name}(`);
  const end = source.indexOf("\nfunction ", start + 1);
  const context = vm.createContext(globals);
  vm.runInContext(source.slice(start, end), context);
  return context[name];
}

Deno.test("sets: removing a failed slide preserves the selected item and removes an empty set", () => {
  const items = [{id: 1}, {id: 2}, {id: 3}];
  const media = [{}, {}, {}];
  const track = {children: [], scrollTo() {}};
  const slides = items.map((_, index) => ({
    offsetLeft: index * 100, setAttribute() {},
    remove() { track.children.splice(track.children.indexOf(this), 1); },
  }));
  track.children = [...slides];
  let removed = false;
  const card = {_setItems: [...items], _setMedia: [...media], _setTrack: track, _item: items[1], remove() {removed = true;}};
  const remove = getFunction("sets", "removeUnavailableSetItem", {
    activateSlide(card, i) {card._setIndex = i; card._item = card._setItems[i];},
  });
  remove(card, media[0], slides[0]);
  assert.equal(card._item, items[1]);
  assert.equal(card._setIndex, 0);
  remove(card, media[1], slides[1]);
  assert.equal(card._item, items[2]);
  remove(card, media[2], slides[2]);
  assert.equal(removed, true);
  assert.equal(track.children.length, 0);
});

Deno.test("scroll: hiding the active row advances; hiding the last row leaves a temporary-unavailable message", () => {
  const cell = {remove() {}, closest() {return row;}};
  const row = {_cells: [cell], _progressBars: []};
  const next = {offsetTop: 0};
  const state = {cards: [row, next], activeCard: row};
  let disposed = 0, message = "";
  const remove = getFunction("scroll", "removeUnavailableCell", {
    state, disposeRow() {disposed++;}, setActiveCard(row) {state.activeCard = row;},
    $() {return {scrollTo() {}};}, clearAutoplayTimers() {},
    announce(text) {message = text;},
  });
  remove(cell);
  assert.equal(state.activeCard, next);
  assert.equal(disposed, 1);
  const last = {remove() {}, closest() {return next;}};
  next._cells = [last];
  remove(last);
  assert.equal(state.cards.length, 0);
  assert.equal(state.activeCard, null);
  assert.match(message, /temporarily unavailable/);
});
