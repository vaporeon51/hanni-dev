// Run: deno test --allow-read tests/site_navigation_test.js
import assert from "node:assert/strict";
import vm from "node:vm";

const settle = async () => { await new Promise(resolve => setTimeout(resolve, 0)); };

function browser(href) {
  const listeners = {};
  const location = {href};
  const entries = [{href, state: {sorterView: "setup"}}];
  let index = 0;
  const history = {
    get state() { return entries[index].state; },
    replaceState(state, _title, next) { entries[index] = {href: new URL(next, location.href).href, state}; location.href = entries[index].href; },
    pushState(state, _title, next) { entries.splice(index + 1); entries.push({href: new URL(next, location.href).href, state}); index++; location.href = entries[index].href; },
    back() { index--; location.href = entries[index].href; listeners.popstate?.({state: this.state}); },
    forward() { index++; location.href = entries[index].href; listeners.popstate?.({state: this.state}); },
  };
  const window = {location, history, addEventListener: (type, fn) => {listeners[type] = fn;}, setTimeout, clearTimeout};
  const context = vm.createContext({window, URL, URLSearchParams, Date, Math});
  vm.runInContext(Deno.readTextFileSync("static/site-navigation.js"), context);
  return {context, window, location, history, entries, navigation: window.SiteNavigation};
}

Deno.test("shared URLs encode q, preserve unrelated filters and ranking hashes, and avoid duplicate entries", () => {
  const b = browser("https://test/sorter?kind=idols#ranking=private-snapshot");
  b.navigation.write(b.navigation.filterUrl({query: "IU & aespa / café"}));
  const url = new URL(b.location.href);
  assert.equal(url.searchParams.get("q"), "IU & aespa / café");
  assert.equal(url.searchParams.get("kind"), "idols");
  assert.equal(url.hash, "#ranking=private-snapshot");
  assert.equal(b.history.state.sorterView, "setup");
  b.navigation.write(b.navigation.filterUrl({query: "IU & aespa / café"}));
  assert.equal(b.entries.length, 2);
  b.navigation.write(b.navigation.filterUrl({query: ""}));
  assert.equal(new URL(b.location.href).searchParams.has("q"), false);
});

Deno.test("URL normalization trims searches, validates sort, and replaces without adding history", () => {
  const b = browser("https://test/feed?q=%20IU%20&sort=invalid");
  b.navigation.normalize({sorts: ["random", "latest"], defaultSort: "random"});
  assert.equal(b.navigation.readQuery(), "IU");
  assert.equal(b.navigation.readChoice("sort", ["random", "latest"], "random"), "random");
  assert.equal(b.entries.length, 1);
  assert.equal(new URL(b.location.href).searchParams.get("sort"), "random");
});

class Element {
  constructor() { this.events = {}; this.dataset = {}; this.attributes = {}; this.value = ""; this.classList = {add() {}, remove() {}}; }
  addEventListener(type, fn) { this.events[type] = fn; }
  setAttribute(name, value) { this.attributes[name] = value; }
  append() {}
  replaceChildren() {}
}

function documentFor(ids, buttons = []) {
  const elements = Object.fromEntries(ids.map(id => [id, new Element()]));
  return {elements, document: {
    body: new Element(), getElementById: id => elements[id], createElement: () => new Element(),
    querySelectorAll: () => buttons, addEventListener() {},
  }};
}

Deno.test("photos restores q on load and history navigation; live typing replaces the URL", async () => {
  const b = browser("https://test/photos?q=Hanni");
  const {elements, document} = documentFor(["search", "wall", "stats"]);
  const items = [{name: "Hanni", group: "NewJeans"}, {name: "Karina", group: "aespa"}];
  b.context.document = document;
  b.context.fetch = async url => ({ok: true, json: async () => url.includes("catalog") ? {entries: items} : {}});
  vm.runInContext(Deno.readTextFileSync("static/photos.js"), b.context);
  await settle();
  assert.equal(elements.search.value, "Hanni");
  assert.ok(elements.wall.innerHTML.includes("Hanni"));
  assert.ok(!elements.wall.innerHTML.includes("Karina"));
  elements.search.value = "Karina";
  elements.search.events.input();
  assert.equal(new URL(b.location.href).searchParams.get("q"), "Karina");
  assert.equal(b.entries.length, 1);
  b.navigation.write(b.navigation.filterUrl({query: "Hanni"}));
  b.history.back();
  assert.equal(elements.search.value, "Karina");
  b.history.forward();
  assert.equal(elements.search.value, "Hanni");
  assert.ok(elements.wall.innerHTML.includes("Hanni"));
});

Deno.test("leaderboard restores kind, supports Back/Forward, and ignores stale tab responses", async () => {
  const b = browser("https://test/leaderboard?kind=groups");
  const buttons = ["idols", "groups"].map(kind => {const e = new Element(); e.dataset.kind = kind; return e;});
  const {elements, document} = documentFor(["board"], buttons);
  b.context.document = document;
  const pending = [];
  b.context.fetch = url => new Promise(resolve => pending.push({url, resolve}));
  vm.runInContext(Deno.readTextFileSync("static/leaderboard.js"), b.context);
  assert.equal(buttons[1].attributes["aria-pressed"], "true");
  buttons[0].events.click();
  assert.equal(new URL(b.location.href).searchParams.get("kind"), "idols");
  pending[1].resolve({ok: true, json: async () => ({entries: []})});
  await settle();
  assert.ok(elements.board.innerHTML.includes("No idols"));
  pending[0].resolve({ok: true, json: async () => ({entries: []})});
  await settle();
  assert.ok(elements.board.innerHTML.includes("No idols"));
  b.history.back();
  assert.equal(buttons[1].attributes["aria-pressed"], "true");
  assert.ok(pending[2].url.includes("kind=groups"));
  pending[2].resolve({ok: true, json: async () => ({entries: []})});
  await settle();
  b.history.forward();
  assert.equal(buttons[0].attributes["aria-pressed"], "true");
  pending[3].resolve({ok: true, json: async () => ({entries: []})});
  await settle();
});

Deno.test("scroll pushes search history, restores q on Back/Forward, and requests q from its API", async () => {
  const b = browser("https://test/scroll");
  const {elements} = documentFor(["query", "scroll-hint", "reel-feed"]);
  const state = {requestToken: 0, retryTimer: null, trimTimer: null, wheelUnlockTimer: null, settleTimer: null, cards: [], seenUrls: new Set(), seenQueue: []};
  const requests = [];
  Object.assign(b.context, {
    navigation: b.navigation, state, BATCH_SIZE: 9, $: id => elements[id],
    document: {createElement: () => new Element()},
    clearAutoplayTimers() {}, disposeRow() {}, closeLightbox() {}, announce() {}, appendItems: () => 0,
    fetch: async url => {requests.push(new URL(url, b.location.href)); return {ok: true, json: async () => ({items: []})};},
  });
  const source = Deno.readTextFileSync("static/scroll.js");
  for (const name of ["loadMore", "resetFeed", "restoredQuery"]) {
    const start = source.search(new RegExp(`(?:async )?function ${name}\\(`));
    const next = source.slice(start + 1).search(/\n(?:async )?function |\nwindow\.addEventListener/);
    vm.runInContext(source.slice(start, start + next + 1), b.context);
  }
  const start = source.indexOf('window.addEventListener("popstate",');
  vm.runInContext(source.slice(start, source.indexOf('\n});', start) + 4), b.context);
  vm.runInContext('resetFeed("Hanni - NewJeans")', b.context);
  await settle();
  assert.equal(new URL(b.location.href).searchParams.get("q"), "Hanni - NewJeans");
  assert.equal(requests[0].searchParams.get("q"), "Hanni - NewJeans");
  vm.runInContext('resetFeed("Hanni - NewJeans")', b.context);
  await settle();
  assert.equal(b.entries.length, 2);
  b.history.back(); await settle();
  assert.equal(elements.query.value, "");
  assert.equal(state.query, "");
  assert.equal(requests.at(-1).searchParams.has("q"), false);
  b.history.forward(); await settle();
  assert.equal(elements.query.value, "Hanni - NewJeans");
  assert.equal(requests.at(-1).searchParams.get("q"), "Hanni - NewJeans");
});
