// Run: deno test --allow-read tests/sorter_engine_test.js
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const engineSource = readFileSync(new URL("../static/sorter/engine.js", import.meta.url), "utf8");
const Sorter = new Function(`${engineSource}; return BiasSorter;`)();
const settings = (ids, focus = 0.6) => ({ name: "adaptive-v1", focus, top: 10, seedOrder: [...ids].reverse() });
function rng(seed) {
  return () => ((seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0) / 2 ** 32);
}
function shuffle(ids, random) {
  ids = [...ids];
  for (let i = ids.length - 1; i > 0; i--) {
    const j = Math.floor(random() * (i + 1));
    [ids[i], ids[j]] = [ids[j], ids[i]];
  }
  return ids;
}
function complete(sorter, answer = ([a, b]) => a < b ? "left" : "right") {
  const choices = [], matchups = [];
  while (!sorter.result) {
    const pair = sorter.pair(), choice = answer(pair);
    choices.push(choice);
    matchups.push(pair);
    sorter.choose(choice);
    assert.ok(choices.length <= 20000, "sort terminates");
  }
  return { choices, matchups };
}

Deno.test("legacy sessions retain exact merge replay and review behavior", () => {
  const ids = [8, 2, 9, 1, 4];
  const sorter = Sorter.create(ids);
  const { choices } = complete(sorter);
  assert.deepEqual(sorter.result.flat(), [1, 2, 4, 8, 9]);
  assert.deepEqual(Sorter.replay(ids, choices).result, sorter.result);
  assert.deepEqual(Sorter.ranking(sorter, [[{ a: 1, b: 2, winner: 2 }]]).flat(), [2, 1, 4, 8, 9]);
  assert.deepEqual(sorter.result.flat(), [1, 2, 4, 8, 9]);
});

Deno.test("coverage is connected, distinct, and gives every item three opponents where possible", () => {
  const random = rng(19);
  for (let n = 2; n <= 140; n++) {
    const ids = shuffle(Array.from({ length: n }, (_, i) => i), random);
    const sorter = Sorter.create(ids, { ...settings(ids), seedOrder: shuffle(ids, random) });
    const edges = new Set(), counts = new Map(ids.map((id) => [id, 0]));
    for (const [a, b] of sorter.coverage) {
      assert.notEqual(a, b);
      const key = sorter.key(a, b);
      assert.ok(!edges.has(key));
      edges.add(key);
      counts.set(a, counts.get(a) + 1);
      counts.set(b, counts.get(b) + 1);
    }
    assert.ok(Math.min(...counts.values()) >= Math.min(3, n - 1));
    assert.ok(sorter.coverage.length <= sorter.budget);
    const reached = new Set([ids[0]]);
    for (let pass = 0; pass < n; pass++) for (const [a, b] of sorter.coverage) {
      if (reached.has(a) || reached.has(b)) { reached.add(a); reached.add(b); }
    }
    assert.equal(reached.size, n);
  }
});

Deno.test("seed order changes opening opponents but never contributes personal rating evidence", () => {
  const ids = [8, 2, 9, 1, 4, 7];
  const first = settings(ids), second = { ...first, seedOrder: [8, 9, 2, 4, 1, 7] };
  const a = Sorter.create(ids, first), b = Sorter.create(ids, second);
  assert.notDeepEqual(a.pair(), b.pair());
  assert.deepEqual(a.scores, ids.map(() => 0));
  assert.deepEqual(a.scores, b.scores);
  const { choices, matchups } = complete(a);
  const sameAnswersDifferentSeed = Sorter.replay(ids, choices, second, matchups);
  assert.deepEqual(a.scores, sameAnswersDifferentSeed.scores);
  assert.deepEqual(a.result, sameAnswersDifferentSeed.result);
});

Deno.test("adaptive undo/resume/share replay produces identical next matchups and final rankings", () => {
  const ids = shuffle(Array.from({ length: 80 }, (_, i) => i), rng(32));
  const algorithm = settings(ids), sorter = Sorter.create(ids, algorithm);
  const choices = [], matchups = [];
  while (!sorter.result) {
    if (choices.length % 37 === 0) {
      const snapshot = JSON.parse(JSON.stringify({ ids, algorithm, choices, matchups }));
      const replay = Sorter.replay(snapshot.ids, snapshot.choices, snapshot.algorithm, snapshot.matchups);
      assert.deepEqual(replay.pair(), sorter.pair());
    }
    const pair = sorter.pair(), choice = pair[0] < pair[1] ? "left" : "right";
    choices.push(choice); matchups.push(pair); sorter.choose(choice);
  }
  const replay = Sorter.replay(ids, choices, algorithm, matchups);
  assert.deepEqual(replay.result, sorter.result);
  const undone = Sorter.replay(ids, choices.slice(0, -1), algorithm, matchups.slice(0, -1));
  assert.deepEqual(undone.pair(), matchups.at(-1));
  undone.choose(choices.at(-1));
  assert.deepEqual(undone.result, sorter.result);
  assert.deepEqual([...sorter.result.flat()].sort((a, b) => a - b), [...ids].sort((a, b) => a - b));
});

Deno.test("ties remain ties, contradictions stay finite, and reviews incorporate outsiders", () => {
  const ids = Array.from({ length: 16 }, (_, i) => i);
  const tied = Sorter.create(ids, settings(ids));
  complete(tied, () => "tie");
  assert.equal(tied.result.length, 1);
  assert.equal(tied.result[0].length, ids.length);
  const sorter = Sorter.create(ids, settings(ids));
  complete(sorter);
  const original = sorter.result.flat(), outsider = original.at(-1);
  const reviews = [original.slice(0, 10).map((a) => ({ a, b: outsider, winner: outsider }))];
  const refined = Sorter.ranking(sorter, reviews).flat();
  assert.ok(refined.indexOf(outsider) < original.indexOf(outsider));
  assert.deepEqual(sorter.result.flat(), original, "reviewing does not mutate the original session");
  const model = sorter.withReviews(reviews);
  assert.ok(model.scores.every(Number.isFinite));
  const pair = model.selectPair(true, true);
  assert.ok(pair && pair[0] !== pair[1]);
  assert.ok(!model.history.slice(-3).some((h) => model.key(...h.pair) === model.key(...pair)));
});

Deno.test("invalid adaptive sessions fail explicitly instead of being replayed as merge sort", () => {
  const ids = [1, 2, 3, 4], algorithm = settings(ids);
  assert.throws(() => Sorter.create(ids, { ...algorithm, name: "future-v2" }));
  assert.throws(() => Sorter.create(ids, { ...algorithm, seedOrder: [1, 1, 3, 4] }));
  assert.throws(() => Sorter.replay(ids, ["left"], algorithm));
  assert.throws(() => Sorter.replay(ids, ["left"], algorithm, [[1, 99]]));
  assert.throws(() => Sorter.replay(ids, ["tie"], algorithm, [[1, 1]]));
});

Deno.test("focus dial improves top precision at fixed effort in reproducible noisy simulations", () => {
  const metrics = [];
  for (const focus of [0, 0.6]) {
    const random = rng(129);
    let recall = 0, topError = 0, exposure = 0, comparisons = 0;
    for (let run = 0; run < 8; run++) {
      const ids = shuffle(Array.from({ length: 128 }, (_, i) => i), random);
      const sorter = Sorter.create(ids, { ...settings(ids, focus), seedOrder: shuffle(ids, random) });
      complete(sorter, ([a, b]) => {
        if (sorter.comparisons >= sorter.coverage.length) exposure += Number(Math.min(a, b) < 15);
        const flip = random() < 0.03;
        return (a < b) !== flip ? "left" : "right";
      });
      const top = sorter.result.flat().slice(0, 10);
      recall += top.filter((id) => id < 10).length / 10;
      topError += top.reduce((sum, id, i) => sum + Math.abs(id - i), 0) / 10;
      comparisons += sorter.comparisons;
    }
    metrics.push({ recall, topError, exposure, comparisons });
  }
  assert.equal(metrics[0].comparisons, metrics[1].comparisons);
  assert.ok(metrics[1].recall > metrics[0].recall);
  assert.ok(metrics[1].topError < metrics[0].topError);
  assert.ok(metrics[1].exposure > metrics[0].exposure * 1.5);
});

async function browserFixture(saved = null, boardFails = false, hash = "") {
  const catalog = JSON.parse(readFileSync(new URL("../static/sorter/catalog.json", import.meta.url), "utf8"));
  const ids = catalog.entries.filter((item) => item.kind === "idol").slice(0, 16).map((item) => item.id);
  const elements = new Map();
  const element = (id) => {
    if (!elements.has(id)) elements.set(id, {
      id, value: id === "result-images" ? "10" : "", hidden: false, disabled: false,
      dataset: {}, classList: { toggle() {}, add() {}, remove() {} },
      innerHTML: "", textContent: "", setAttribute() {}, removeAttribute() {},
      addEventListener(type, fn) { if (type === "click") this.onclick = fn; }, querySelectorAll() { return []; }, querySelector() { return null; },
      contains() { return false; }, focus() {}, appendChild() {},
      click() { this.onclick?.({ currentTarget: this }); },
    });
    return elements.get(id);
  };
  const storage = new Map([["bias-club-lineup-v1", JSON.stringify({ ids, mode: "idols", savedAt: Date.now() })]]);
  if (saved) storage.set("bias-club-session-v1", JSON.stringify(saved));
  const document = {
    body: { dataset: {} }, readyState: "complete", activeElement: null,
    getElementById: element, querySelectorAll() { return []; }, querySelector() { return null; }, addEventListener() {},
    createElement() { return element("created"); },
  };
  const context = vm.createContext({
    document, console, URL, Blob, AbortController,
    localStorage: { getItem: (key) => storage.get(key) || null, setItem: (key, value) => storage.set(key, value), removeItem: (key) => storage.delete(key) },
    location: { hash, search: "", pathname: "/sorter", origin: "https://example.test" },
    history: { replaceState() { context.location.hash = ""; } },
    window: { scrollTo() {}, matchMedia: () => ({ matches: true }) },
    navigator: { sendBeacon() { return true; }, clipboard: { async writeText(url) { context.copiedURL = url; } } },
    Image: class {}, setTimeout() { return 0; }, clearTimeout() {},
    fetch: async (url) => {
      if (url.includes("leaderboard")) {
        if (boardFails) throw new Error("offline");
        return { ok: true, json: async () => ({ entries: [] }) };
      }
      return { ok: true, json: async () => url.includes("catalog") ? catalog : {} };
    },
  });
  vm.runInContext(engineSource, context);
  vm.runInContext(readFileSync(new URL("../static/sorter/lz-string.min.js", import.meta.url), "utf8"), context);
  vm.runInContext(readFileSync(new URL("../static/sorter/sorter.js", import.meta.url), "utf8"), context);
  for (let i = 0; i < 30; i++) await Promise.resolve();
  return { element, storage, ids, context, document, catalog,
    session: () => JSON.parse(storage.get("bias-club-session-v1")) };
}

Deno.test("progress links transfer choices and persist on the receiving device; result links still open", async () => {
  const source = await browserFixture();
  source.element("start").click();
  source.element("pick-left").click();
  source.element("pick-right").click();
  await source.element("continue-link").onclick();
  const hash = new URL(source.context.copiedURL).hash;
  assert.ok(hash.startsWith("#continue="));
  const target = await browserFixture(null, false, hash);
  assert.deepEqual(target.session().choices, source.session().choices);
  assert.deepEqual(target.session().matchups, source.session().matchups);
  assert.equal(target.element("pick-left").innerHTML, source.element("pick-left").innerHTML);
  assert.equal(target.context.location.hash, "");
  target.element("pick-left").click();
  const refreshed = await browserFixture(target.session(), false, target.context.location.hash);
  refreshed.element("resume").click();
  assert.deepEqual(refreshed.session().choices, target.session().choices);
  while (!target.session().finished) target.element("pick-left").click();
  await target.element("share").onclick();
  const resultHash = new URL(target.context.copiedURL).hash;
  assert.ok(resultHash.startsWith("#ranking="));
  const result = await browserFixture(null, false, resultHash);
  assert.equal(result.element("results").hidden, false);
  assert.deepEqual(result.session().choices, target.session().choices);
});

Deno.test("browser flow supports offline seeding, sort undo, adaptive review undo/resume, and results", async () => {
  let ui = await browserFixture(null, true);
  assert.equal(ui.element("start").disabled, false);
  ui.element("start").click();
  let session = ui.session();
  assert.equal(session.algorithm.name, "adaptive-v1");
  assert.equal(session.ids.length, 16);
  const seedOrder = session.algorithm.seedOrder;
  ui.element("pick-left").click();
  ui.element("undo").click();
  assert.equal(ui.session().choices.length, 0);
  assert.equal(ui.session().matchups.length, 0);
  for (let i = 0; i < 200 && ui.element("results").hidden; i++) ui.element("pick-left").click();
  assert.equal(ui.element("results").hidden, false);
  assert.deepEqual(ui.session().algorithm.seedOrder, seedOrder);
  ui.element("verify").click();
  ui.element("pick-right").click();
  assert.equal(ui.session().verify.picks.length, 1);
  ui.element("undo").click();
  assert.equal(ui.session().verify.picks.length, 0);
  assert.equal(ui.session().verify.pairs.length, 1);
  ui.element("pick-left").click();
  session = ui.session();
  const expectedPair = session.verify.pairs.at(-1);
  ui = await browserFixture(session);
  ui.element("resume").click();
  ui.element("verify").click();
  assert.deepEqual(ui.session().verify.pairs.at(-1), expectedPair);
  for (let i = 0; i < 12 && ui.element("results").hidden; i++) ui.element("pick-left").click();
  assert.equal(ui.element("results").hidden, false);
  assert.equal(ui.session().verifyRounds.length, 1);
  assert.equal(ui.session().verify, null);
  // The Mine tab must use the same completed review evidence as the sorter.
  const reviewed = ui.session();
  const expected = Sorter.ranking(Sorter.replay(reviewed.ids, reviewed.choices,
    reviewed.algorithm, reviewed.matchups), reviewed.verifyRounds).flat();
  const mine = ui.element("mine-tab"); mine.dataset.scope = "personal";
  ui.document.querySelectorAll = (selector) => selector === "[data-scope]" ? [mine] : [];
  vm.runInContext(readFileSync(new URL("../static/leaderboard.js", import.meta.url), "utf8"), ui.context);
  for (let i = 0; i < 10; i++) await Promise.resolve();
  mine.click();
  for (let i = 0; i < 20; i++) await Promise.resolve();
  const html = ui.element("board").innerHTML;
  const champion = ui.catalog.entries.find((entry) => entry.id === expected[0]);
  assert.ok(html.includes("your sorter ranking"));
  assert.ok(html.includes(champion.short || champion.name));
  assert.ok(!html.includes("Ranking in progress"));
  ui.element("undo-final").click();
  assert.equal(ui.session().verifyRounds.length, 0);
  assert.equal(ui.session().choices.length, ui.session().matchups.length);
  assert.equal(ui.element("sorting").hidden, false);
});

Deno.test("completed rankings survive expiry and remain available until a new sort starts", async () => {
  let ui = await browserFixture();
  ui.element("start").click();
  const unfinished = ui.session();
  for (let i = 0; i < 200 && ui.element("results").hidden; i++) ui.element("pick-left").click();
  const completed = ui.session();
  const weekAgo = Date.now() - 7 * 24 * 60 * 60 * 1000;
  completed.savedAt = completed.finished = weekAgo;
  ui = await browserFixture(completed);
  assert.equal(ui.element("resume-banner").hidden, false);
  assert.equal(ui.element("resume").textContent, "View results →");
  assert.ok(ui.element("resume-message").textContent.includes("Your ranking is ready"));
  assert.deepEqual(ui.session(), completed, "visiting setup does not rewrite the saved ranking");
  ui.element("resume").click();
  assert.equal(ui.element("results").hidden, false);
  ui.element("new-lineup").click();
  assert.equal(ui.element("setup").hidden, false);
  assert.equal(ui.element("resume").textContent, "View results →");
  assert.deepEqual(ui.session().choices, completed.choices, "editing lineup preserves the result");
  ui.element("start").click();
  assert.equal(ui.session().finished, undefined);
  assert.equal(ui.session().choices.length, 0);
  ui.element("pause").click();
  assert.equal(ui.element("resume").textContent, "Continue ranking →");
  assert.ok(ui.element("resume-message").textContent.includes("in progress"));

  unfinished.savedAt = weekAgo;
  ui = await browserFixture(unfinished);
  assert.equal(ui.element("resume-banner").hidden, true);
  assert.equal(ui.storage.has("bias-club-session-v1"), false);
});
