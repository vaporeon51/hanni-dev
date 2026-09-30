// Run: deno test --allow-read tests/disambiguation_ui_test.js
import assert from 'node:assert/strict';
import vm from 'node:vm';

class Element {
  constructor(tag = 'div') {
    this.tagName = tag.toUpperCase(); this.children = []; this.events = {};
    this.classes = new Set(); this.classList = {add: name => this.classes.add(name), remove: name => this.classes.delete(name)};
  }
  get isConnected() { return this.root || this.parent?.isConnected || false; }
  append(...children) { children.forEach(child => { child.parent = this; this.children.push(child); }); }
  replaceChildren(...children) { this.children.forEach(child => { child.parent = null; }); this.children = []; this.append(...children); }
  setAttribute(name, value) { this[name] = value; }
  removeAttribute(name) { delete this[name]; }
  addEventListener(name, callback) { this.events[name] = callback; }
  querySelectorAll(tag) { return this.children.flatMap(child => [...(child.tagName === tag.toUpperCase() ? [child] : []), ...child.querySelectorAll(tag)]); }
  load() { this.loads = (this.loads || 0) + 1; }
  pause() { this.pauses = (this.pauses || 0) + 1; }
  play() { return Promise.resolve(); }
}
const settle = async () => { for (let i = 0; i < 3; i++) await new Promise(resolve => setTimeout(resolve, 0)); };

function harness(item, metadata) {
  const ids = Object.fromEntries(['status', 'queue', 'review', 'search', 'set-list', 'wall', 'progress', 'back'].map(id => {
    const el = new Element(); el.root = true; el.value = ''; return [id, el];
  }));
  const requests = [], observers = [], listeners = {};
  const location = {pathname: '/disambiguation', search: ''};
  const response = data => ({ok: true, status: 200, json: async () => structuredClone(data)});
  const context = vm.createContext({
    AbortController, URLSearchParams, Promise, console, location,
    document: {getElementById: id => ids[id], createElement: tag => new Element(tag), createTextNode: text => { const e = new Element('text'); e.textContent = text; return e; }},
    window: {addEventListener: (event, handler) => { listeners[event] = handler; }, scrollTo() {}},
    history: {
      replaceState(_state, _title, url) { location.search = url.includes('?') ? url.slice(url.indexOf('?')) : ''; },
      pushState(_state, _title, url) { location.search = url; },
      back() { location.search = ''; listeners.popstate(); },
    },
    IntersectionObserver: class {
      constructor(callback) { this.callback = callback; this.targets = new Set(); observers.push(this); }
      observe(target) { this.targets.add(target); }
      unobserve(target) { this.targets.delete(target); }
      disconnect() { this.targets.clear(); }
    },
    fetch: async (url, options = {}) => {
      requests.push({url, options});
      if (options.method === 'POST') return response({updated: 2});
      if (url.endsWith('/media')) return metadata ? await metadata(options) : response({kind: 'image', url: '/api/disambiguation/1/asset'});
      if (url.endsWith('/1')) return response({key: 'set-1', items: [item]});
      return response({sets: [{key: 'set-1', anchor_id: 1, size: 1, remaining: 1, reports: 1}]});
    },
  });
  vm.runInContext(Deno.readTextFileSync('static/media-player.js'), context);
  vm.runInContext(Deno.readTextFileSync('static/disambiguation.js'), context);
  return {ids, requests, observers,
    async open() { await settle(); await ids['set-list'].children[0].onclick(); await settle(); },
    async preview() { const loader = observers[1]; loader.callback([...loader.targets].map(target => ({target, isIntersecting: true}))); await settle(); },
  };
}
const item = preview => ({id: 1, url: 'https://i.imgur.com/video.mp4', reviewed: false, preview,
  roles: [{id: 'a', name: 'A', label: 'A (Group)', selected: false}, {id: 'b', name: 'B', label: 'B (Group)', selected: false}]});

Deno.test('review: video uses shared player and proxy; marking broken stops playback', async () => {
  const h = harness(item({kind: 'video', url: 'https://i.imgur.com/video.mp4'}));
  await h.open(); await h.preview();
  const video = h.ids.wall.querySelectorAll('video')[0];
  assert.equal(video.src, '/api/disambiguation/1/asset');
  assert.equal(video.preload, 'auto'); assert.equal(video.playsInline, true); assert.equal(video.loads, 1);
  video.onloadeddata();
  const buttons = h.ids.wall.querySelectorAll('button');
  await buttons[1].onclick();
  assert.equal(video.pauses, 1); assert.equal(video.src, undefined);
  assert.equal(h.ids.progress.textContent, '0 photos · 0 left');
  assert.equal(buttons[0].disabled, true);
});

Deno.test('review: explicit selection saves once; later edits show unsaved changes', async () => {
  const h = harness(item({kind: 'image', url: 'https://i.imgur.com/photo.jpg'}));
  await h.open();
  const form = h.ids.wall.querySelectorAll('form')[0];
  const inputs = form.querySelectorAll('input');
  await form.onsubmit({preventDefault() {}});
  assert.equal(h.requests.filter(r => r.options.method === 'POST').length, 0);
  inputs[0].checked = true;
  await form.onsubmit({preventDefault() {}});
  const post = h.requests.find(r => r.options.method === 'POST');
  assert.deepEqual(JSON.parse(post.options.body), {selected: ['a'], expected: ['a', 'b']});
  assert.equal(h.ids.progress.textContent, '1 photos · 0 left');
  form.events.change();
  assert.equal(form.querySelectorAll('p')[0].textContent, 'Unsaved changes');
});

Deno.test('review: leaving a set cancels pending previews and uses cached set data on return', async () => {
  let finish, signal;
  const h = harness(item(null), options => {
    signal = options.signal;
    return new Promise(resolve => { finish = () => resolve({ok: true, status: 200, json: async () => ({kind: 'video', url: '/api/disambiguation/1/asset'})}); });
  });
  await h.open(); await h.preview();
  h.ids.back.onclick(); await settle();
  assert.equal(signal.aborted, true);
  finish(); await settle();
  assert.equal(h.ids.wall.children.length, 0);
  assert.equal(h.ids.review.hidden, true);
  await h.ids['set-list'].children[0].onclick();
  assert.equal(h.requests.filter(r => r.url.endsWith('/1')).length, 1);
});

Deno.test('review: preview failures do not overwrite a successful save', async () => {
  const h = harness(item({kind: 'image', url: 'https://i.imgur.com/photo.jpg'}));
  await h.open(); await h.preview();
  const form = h.ids.wall.querySelectorAll('form')[0];
  form.querySelectorAll('input')[0].checked = true;
  await form.onsubmit({preventDefault() {}});
  const image = h.ids.wall.querySelectorAll('img')[0];
  image.onerror(); image.onerror();
  assert.equal(form.querySelectorAll('p')[0].textContent, 'Saved');
});
