// Run: deno test --allow-read tests/role_filter_ui_test.js
import assert from "node:assert/strict";
import vm from "node:vm";

const settle = async () => { await new Promise(resolve => setTimeout(resolve, 0)); };

function harness(view, search = "", { renderMedia = false, empty = false, kind = "image" } = {}) {
  class Element {
    constructor(tag = "div") {
      this.tagName = tag.toUpperCase();
      this.children = []; this.events = {}; this.dataset = {}; this.value = "";
      this.className = ""; this.style = { removeProperty() {} };
      this.classList = { contains: name => this.className.split(" ").includes(name) };
    }
    get childNodes() { return this.children; }
    get firstChild() { return this.children[0]; }
    append(...nodes) { nodes.forEach(node => {
      if (typeof node === "string") { const text = new Element("text"); text.textContent = node; node = text; }
      node.parent = this; this.children.push(node);
    }); }
    appendChild(node) { this.append(node); }
    removeChild(node) { this.children = this.children.filter(child => child !== node); node.parent = null; }
    remove() { this.parent?.removeChild(this); }
    replaceChildren(...nodes) { this.children.forEach(node => {node.parent = null;}); this.children = []; this.append(...nodes); }
    matches(selector) {
      if (selector.startsWith(".")) return this.className.split(" ").includes(selector.slice(1));
      if (selector === 'button[type="submit"]') return this.tagName === "BUTTON" && this.type === "submit";
      const id = selector.match(/^\[data-content-link-id="(.+)"\]$/);
      if (id) return this.dataset.contentLinkId === id[1];
      return this.tagName.toLowerCase() === selector;
    }
    querySelectorAll(selector) { return this.children.flatMap(child => [...(child.matches(selector) ? [child] : []), ...child.querySelectorAll(selector)]); }
    querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
    addEventListener(type, callback) { this.events[type] = callback; }
    setAttribute(name, value) { this[name] = value; }
    removeAttribute(name) { delete this[name]; }
    getAttribute(name) { return this[name] || null; }
    blur() {}
    pause() {}
    load() {}
    play() { return Promise.resolve(); }
    getBoundingClientRect() { return {top: 9999, height: 100}; }
  }
  const elements = Object.fromEntries(["query", "sort", "feed", "feed-form", "sets-form", "status", "feed-sentinel", "timeline-tools", "timeline-search", "timeline-top", "collection-heading"].map(id => [id, new Element()]));
  for (const id of ["feed-form", "sets-form"]) {
    const button = new Element("button"); button.type = "submit"; elements[id].append(button);
  }
  const listeners = {}, requests = [], entries = [{url: `https://test/${view === "app" ? "feed" : "sets"}${search}`, state: null}];
  let index = 0;
  const location = {href: entries[0].url};
  const history = {
    get state() { return entries[index].state; }, scrollRestoration: "auto",
    replaceState(state, _title, url) { entries[index] = {state, url: new URL(url, location.href).href}; location.href = entries[index].url; },
    pushState(state, _title, url) { entries.splice(index + 1); entries.push({state, url: new URL(url, location.href).href}); index++; location.href = entries[index].url; },
    back() { if (index > 0) { index--; location.href = entries[index].url; listeners.popstate({state: this.state}); } },
    forward() { if (index + 1 < entries.length) { index++; location.href = entries[index].url; listeners.popstate({state: this.state}); } },
  };
  const window = {
    location, history, scrollY: 0, innerHeight: 900,
    matchMedia: () => ({matches: false}), addEventListener: (type, callback) => {listeners[type] = callback;},
    scrollTo: ({top}) => {window.scrollY = top;}, requestAnimationFrame: callback => callback(),
    setTimeout, clearTimeout,
  };
  const context = vm.createContext({
    window, URL, URLSearchParams, Intl, Date, Map, Set, AbortController,
    document: {getElementById: id => elements[id], createElement: tag => new Element(tag), documentElement: new Element()},
    fetch: async url => {
      requests.push(new URL(url, location.href));
      if (url.endsWith("/media")) return {ok: true, json: async () => ({kind, url: "/api/feed/42/asset", collection_count: 3})};
      const items = empty ? [] : [{content_link_id: renderMedia ? 42 : requests.length, label: "Hanni - NewJeans", url: "https://example.test/photo"}];
      return {ok: true, json: async () => ({items, sets: empty ? [] : [{collection_of: requests.length}], next_cursor: empty ? null : "next-page"})};
    },
    addMockCards() {
      const card = new Element(); card.className = view === "app" ? "card" : "set-card";
      card._setMedia = []; elements.feed.append(card);
    },
  });
  vm.runInContext(Deno.readTextFileSync("static/site-navigation.js"), context);
  vm.runInContext(Deno.readTextFileSync("static/media-player.js"), context);
  vm.runInContext(Deno.readTextFileSync(`static/${view}.js`), context);
  // Keep real navigation and loading; stand in for media rendering.
  if (!renderMedia) vm.runInContext(view === "app" ? "appendCards = addMockCards" : "appendSetCards = addMockCards", context);
  return {context, elements, requests, history, location, window, entries,
    click(label = "Hanni - NewJeans", modifiers = {}) {
      const role = {textContent: label, dataset: {filterQuery: label}};
      elements.feed.events.click({button: 0, preventDefault() {}, ...modifiers, target: {closest: selector => selector === "a[data-filter-query]" ? role : null}});
    },
    more() { return vm.runInContext(view === "app" ? "loadMoreFeed()" : "loadMoreSets()", context); },
  };
}

for (const [view, sorts] of [["app", ["random", "latest", "top"]], ["sets", ["latest", "oldest"]]]) {
  for (const sort of sorts) {
    Deno.test(`${view}: idol URL keeps ${sort}; pagination, Back, Forward and refresh work`, async () => {
      const h = harness(view, `?sort=${sort}`);
      await settle();
      h.window.scrollY = 720;
      const originalCards = [...h.elements.feed.children];
      h.click();
      await settle();
      const filteredUrl = new URL(h.location.href);
      assert.equal(filteredUrl.searchParams.has("role_id"), false);
      assert.equal(filteredUrl.searchParams.get("q"), "Hanni - NewJeans");
      assert.equal(filteredUrl.searchParams.get("sort"), sort);
      await h.more();
      assert.equal(h.requests.length, 3);
      for (const url of h.requests.slice(1)) {
        assert.equal(url.pathname, view === "app" ? "/api/feed" : "/api/sets");
        assert.equal(url.searchParams.has("role_id"), false);
        assert.equal(url.searchParams.get("sort"), sort);
        assert.equal(url.searchParams.get("q"), "Hanni - NewJeans");
      }
      h.history.back();
      await settle();
      assert.equal(h.elements.query.value, "");
      assert.equal(h.elements.sort.value, sort);
      assert.equal(h.window.scrollY, 720);
      assert.deepEqual(h.elements.feed.children, originalCards);
      assert.equal(h.requests.length, 3);
      h.history.forward();
      await settle();
      assert.equal(h.elements.query.value, "Hanni - NewJeans");
      assert.equal(h.elements.sort.value, sort);
      assert.equal(h.requests.length, 3);
      const refreshed = harness(view, filteredUrl.search);
      await settle();
      assert.equal(refreshed.requests[0].searchParams.get("q"), "Hanni - NewJeans");
      assert.equal(refreshed.elements.sort.value, sort);
      h.elements.query.value = "aespa";
      h.elements[view === "app" ? "feed-form" : "sets-form"].events.submit({preventDefault() {}});
      await settle();
      assert.equal(h.requests[3].searchParams.has("role_id"), false);
      assert.equal(h.requests[3].searchParams.get("q"), "aespa");
      assert.equal(new URL(h.location.href).searchParams.get("q"), "aespa");
    });
  }
  Deno.test(`${view}: sort changes retain the query and can be undone`, async () => {
    const h = harness(view, `?q=Hanni+-+NewJeans&sort=${sorts[0]}`);
    await settle();
    h.elements.sort.value = sorts[1];
    h.elements.sort.events.change({preventDefault() {}});
    await settle();
    assert.equal(new URL(h.location.href).searchParams.get("sort"), sorts[1]);
    assert.equal(h.requests[1].searchParams.get("q"), "Hanni - NewJeans");
    assert.equal(h.requests[1].searchParams.has("role_id"), false);
    h.history.back();
    await settle();
    assert.equal(h.elements.sort.value, sorts[0]);
    assert.equal(h.elements.query.value, "Hanni - NewJeans");
  });
  Deno.test(`${view}: uncached history reads URL filters; modified clicks keep native navigation`, async () => {
    const h = harness(view, `?sort=${sorts[0]}`);
    await settle();
    h.click("A"); await settle();
    h.click("B"); await settle();
    h.history.back(); await settle();
    h.history.back(); await settle();
    assert.equal(h.requests.at(-1).searchParams.has("q"), false);
    assert.equal(h.elements.query.value, "");
    const count = h.entries.length;
    h.click("C", {ctrlKey: true}); await settle();
    assert.equal(h.entries.length, count);
  });
}


for (const view of ["app", "sets"]) {
  Deno.test(`${view}: Back restores an empty result view`, async () => {
    const h = harness(view, "?q=missing", {empty: true});
    await settle();
    const original = [...h.elements.feed.children];
    h.click("Another search"); await settle();
    h.history.back(); await settle();
    assert.equal(h.elements.query.value, "missing");
    assert.deepEqual(h.elements.feed.children, original);
    assert.match(h.elements.status.textContent, /^0 /);
  });
  Deno.test(`${view}: Back during a pending search ignores its late response and enables Search`, async () => {
    const h = harness(view);
    await settle();
    const original = [...h.elements.feed.children];
    let finish;
    h.context.fetch = () => new Promise(resolve => {finish = resolve;});
    h.click();
    h.history.back();
    finish({ok: true, json: async () => ({items: [], sets: []})});
    await settle();
    assert.equal(h.elements.query.value, "");
    assert.deepEqual(h.elements.feed.children, original);
    assert.equal(h.elements[view === "app" ? "feed-form" : "sets-form"].querySelector('button[type="submit"]').disabled, false);
  });
}

Deno.test("feed: full card rendering keeps images/videos and set links through filter navigation", async () => {
  for (const kind of ["image", "video"]) {
    const h = harness("app", "?sort=latest", {renderMedia: true, kind});
    await settle();
    const card = h.elements.feed.querySelector(".card");
    const media = card.querySelector(kind === "image" ? "img" : "video");
    assert.equal(media.src, "/api/feed/42/asset");
    (kind === "image" ? media.onload : media.onloadeddata)();
    assert.equal(card.querySelector(".card-media").className, "card-media is-ready");
    const link = new URL(card.querySelector(".collection-link").href);
    assert.equal(link.searchParams.get("collection"), "42");
    assert.equal(link.searchParams.get("sort"), "latest");
    const title = card.querySelector(".filter-link");
    assert.equal(new URL(title.href).searchParams.get("q"), title.textContent);
    h.click(title.dataset.filterQuery); await settle();
    const filtered = h.elements.feed.querySelector(".card");
    assert.equal(new URL(filtered.querySelector(".collection-link").href).searchParams.get("q"), title.textContent);
    h.history.back(); await settle();
    assert.equal(h.elements.feed.querySelector(".card"), card);
    assert.equal(media.src, "/api/feed/42/asset");
    card._mediaController.dispose();
    filtered._mediaController.dispose();
  }
});
