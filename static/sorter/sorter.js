/* Bias sorter ♡ — hanni web port of kpopidolsorter/src/js/app.js.
 *
 * Differences from the original:
 * - lineup data comes from /static/sorter/catalog.json (kpopping overwrites
 *   baked in at build time) instead of inline data.js + photo-cache.js
 * - completed idol sorts submit one normalized, daily-budgeted Elo ballot
 * - adaptive sessions give everyone coverage, then focus on favorites
 * - lightly seeded opening pairs use consensus only to choose opponents
 * - double-check rounds adapt to new answers and refit the personal ranking
 * - next-pair images are preloaded after each render for instant cards
 *
 * Legacy merge-sort sessions retain their original replay behavior.
 */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const catalogURL = document.body.dataset.catalog || "/static/sorter/catalog.json";
  const embedsURL = document.body.dataset.embeds || "/static/sorter/embed-photos.json";
  // Discord-proxied kpopping portraits (the real kpopping bytes, hotlinkable).
  // Filled at boot; entries without one fall through to vendored portraits.
  const embedPhotos = {};

  const escape = (value) =>
    String(value).replace(
      /[&<>"']/g,
      (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]),
    );
  const fallbackImage =
    "data:image/svg+xml," +
    encodeURIComponent(
      '<svg xmlns="http://www.w3.org/2000/svg" width="180" height="180" viewBox="0 0 180 180"><rect width="180" height="180" fill="#fbe8f0"/><text x="90" y="112" text-anchor="middle" font-size="70" fill="#b84d79">♡</text></svg>',
    );
  document.addEventListener(
    "error",
    (event) => {
      if (event.target.tagName === "IMG" && event.target.src !== fallbackImage) {
        event.target.src = fallbackImage;
        event.target.classList.add("fallback-image");
      }
    },
    true,
  );

  const shortName = (item) => item.short || item.name;
  const groupName = (item) => item.group || "Group";
  // Vendored portraits first (harvested Discord shots, slimmed, served
  // same-origin). Discord-proxied originals cover entries without a vendored
  // file; remote originals are the last resort.
  const imageURL = (item) =>
    item.local || (item.role_id && embedPhotos[item.role_id]) || item.fallback;

  function toast(message) {
    $("toast").textContent = message;
    $("toast").hidden = false;
    clearTimeout(toast.timer);
    toast.timer = setTimeout(() => ($("toast").hidden = true), 3500);
  }
  function read(key) {
    try {
      return JSON.parse(localStorage.getItem(key));
    } catch {
      return null;
    }
  }
  function write(key, value) {
    try {
      localStorage.setItem(key, JSON.stringify(value));
    } catch {
      toast("Storage is unavailable. Keep this tab open to retain your session.");
    }
  }

  const savedStateLifetime = 2 * 60 * 60 * 1000;
  function readSavedState(key, keepCompleted = false) {
    const value = read(key);
    // Finished rankings remain on this device until another sort replaces
    // them. Lineup drafts and unfinished sessions still expire normally.
    if (keepCompleted && Number.isFinite(value?.finished) && value.finished > 0) return value;
    const age = Date.now() - value?.savedAt;
    if (Number.isFinite(value?.savedAt) && age >= 0 && age < savedStateLifetime) return value;
    try { localStorage.removeItem(key); } catch {}
    return null;
  }

  // Drifting heart burst on each pick — the cute payoff for voting.
  function burst(button) {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const heart = document.createElement("span");
    heart.className = "pick-heart";
    heart.textContent = "♡";
    heart.style.left = 30 + Math.random() * 40 + "%";
    button.appendChild(heart);
    setTimeout(() => heart.remove(), 900);
  }


  function preload(item) {
    if (!item) return;
    const url = imageURL(item);
    if (!url || preload.seen.has(url)) return;
    preload.seen.add(url);
    const img = new Image();
    img.decoding = "async";
    img.referrerPolicy = "no-referrer";
    img.src = url;
  }
  preload.seen = new Set();

  const navigation = window.SiteNavigation;
  navigation.normalize();

  async function boot() {
    let data;
    try {
      const response = await fetch(catalogURL, { credentials: "same-origin" });
      if (!response.ok) throw new Error("catalog " + response.status);
      data = await response.json();
    } catch {
      toast("Could not load the idol catalog. Please refresh to try again.");
      return;
    }
    try {
      const response = await fetch(embedsURL, { credentials: "same-origin" });
      if (response.ok) {
        const embeds = await response.json();
        Object.assign(embedPhotos, (embeds && embeds.by_role) || {});
      }
    } catch {
      /* vendored portraits cover everything */
    }
    const dataSetVersion = data.version || "2025-11-01";
    const catalog = data.entries;
    const byId = new Map(catalog.map((item) => [item.id, item]));
    const definitions = data.groups || [];
    const groupPhotos = catalog.filter((i) => i.kind === "group");
    const photoByLabel = new Map();
    groupPhotos.forEach((card) => {
      (card.group_labels || [card.group]).forEach((label) => {
        if (!photoByLabel.has(label)) photoByLabel.set(label, card);
      });
    });
    const groups = definitions
      .map((g) => ({
        ...g,
        members: catalog.filter((i) => i.kind === "idol" && (i.groups || []).includes(g.key)),
        photo:
          photoByLabel.get(g.key) ||
          photoByLabel.get(g.name) ||
          catalog.find((i) => i.kind === "idol" && (i.groups || []).includes(g.key)),
      }))
      .filter((g) => g.members.length);
    // Soloist definitions have no generation tags. Keep them in idol selection,
    // but exclude them from group rankings.
    const groupSortIds = new Set(groups.filter((g) => g.gen?.length).map((g) => g.photo?.id).filter(Number.isInteger));
    // Unpinned alphabetical order until the live board resolves. The boot
    // tail paints from a cached order instantly when one exists (no flicker);
    // a cold start shows a skeleton until the board resolves (bounded wait).
    groups.sort((a, b) => a.name.localeCompare(b.name));

    function normName(value) {
      return (value || "").toLowerCase().replace(/[^a-z0-9]/g, "");
    }
    const aliases = { idle: "gidle", ohmygirl: "omg" };
    aliases[normName("Girls' Generation")] = normName("SNSD");
    const resolve = (value) => aliases[normName(value)] || normName(value);
    let eloBoard = null;
    let idolSeedBoard = null;
    // Best effort and bounded: starting a sort never waits for consensus.
    const seedController = new AbortController();
    const seedTimeout = setTimeout(() => seedController.abort(), 1500);
    fetch("/api/leaderboard?kind=idols&limit=200&include_provisional=false", { credentials: "same-origin", signal: seedController.signal })
      .then((response) => response.ok ? response.json() : null)
      .then((board) => { if (Array.isArray(board?.entries)) idolSeedBoard = board; })
      .catch(() => {}).finally(() => clearTimeout(seedTimeout));
    const adaptiveDefaults = { name: "adaptive-v1", focus: 0.6, top: 10 };
    const seedStrength = 0.25;
    function newAlgorithm(ids) {
      const ratings = new Map();
      if (mode === "idols") {
        const roles = new Map((idolSeedBoard?.entries || []).map((e) => [e.role_id, e.elo]));
        ids.forEach((id) => {
          const value = roles.get(byId.get(id).leaderboard_id || byId.get(id).role_id);
          if (Number.isFinite(value)) ratings.set(id, value);
        });
      } else {
        const names = new Map((eloBoard?.entries || []).map((e) => [resolve(e.group_name), e.elo]));
        ids.forEach((id) => {
          const item = byId.get(id);
          const labels = [...(item.group_labels || []), item.group, item.name];
          const value = labels.map((label) => names.get(resolve(label))).find(Number.isFinite);
          if (Number.isFinite(value)) ratings.set(id, value);
        });
      }
      const values = [...ratings.values()];
      const seeded = ids.map((id) => {
        const rating = ratings.get(id);
        // Missing leaderboard entries are unknown, never assumed to be last.
        // Equal ratings receive equal percentile hints.
        const hint = ratings.has(id) && values.length > 1
          ? (values.filter((v) => v > rating).length +
              (values.filter((v) => v === rating).length - 1) / 2) / (values.length - 1)
          : Math.random();
        return { id, key: seedStrength * hint + (1 - seedStrength) * Math.random() };
      });
      seeded.sort((a, b) => a.key - b.key);
      return { ...adaptiveDefaults, seedOrder: seeded.map((entry) => entry.id) };
    }
    // Lineup order follows the mode: idols mode ranks groups by their peak
    // member ELO; groups mode mirrors the Groups leaderboard tab (top-3
    // average) so the two orders deliberately differ.
    function orderGroups() {
      if (!eloBoard) return;
      const byScore = [...eloBoard.entries].sort((a, b) =>
        mode === "groups" ? b.elo - a.elo : (b.peak_elo ?? b.elo) - (a.peak_elo ?? a.elo),
      );
      const order = new Map();
      byScore.forEach((entry, index) => {
        const key = resolve(entry.group_name);
        if (!order.has(key)) order.set(key, index);
      });
      const rankOf = (g) => {
        for (const candidate of [g.key, g.name]) {
          const key = resolve(candidate);
          if (order.has(key)) return order.get(key);
        }
        return Infinity;
      };
      groups.sort((a, b) => rankOf(a) - rankOf(b) || a.name.localeCompare(b.name));
    }
    const orderKey = "bias-club-group-order-v1";
    const orderCacheLifetime = 7 * 24 * 60 * 60 * 1000;
    function groupsSignature() {
      return groups.map((g) => g.key).join("|");
    }
    function readOrderCache() {
      const value = read(orderKey);
      if (!value || value.mode !== mode || !Array.isArray(value.keys)) return null;
      const age = Date.now() - value.savedAt;
      if (!Number.isFinite(value.savedAt) || age < 0 || age >= orderCacheLifetime) return null;
      if (!value.keys.length || !value.keys.every((k) => typeof k === "string")) return null;
      return value.keys;
    }
    function writeOrderCache() {
      write(orderKey, { mode, keys: groups.map((g) => g.key), savedAt: Date.now() });
    }
    // Applies the cached order when it still covers the catalog (rebuilds
    // add groups). Returns true when the first paint can go out immediately.
    function applyCachedOrder() {
      const keys = readOrderCache();
      if (!keys) return false;
      const position = new Map(keys.map((k, i) => [k, i]));
      if (!groups.every((g) => position.has(g.key))) return false;
      groups.sort((a, b) => position.get(a.key) - position.get(b.key));
      return true;
    }
    async function fetchEloOrder(timeoutMs) {
      try {
        const response = await Promise.race([
          fetch("/api/leaderboard?kind=groups&limit=200", { credentials: "same-origin" }),
          new Promise((_, reject) => setTimeout(() => reject(new Error("order timeout")), timeoutMs)),
        ]);
        if (!response.ok) return false;
        const board = await response.json();
        if (!board || !Array.isArray(board.entries)) return false;
        eloBoard = board;
        orderGroups();
        writeOrderCache();
        return true;
      } catch {
        return false;
      }
    }
    // Background refresh for the warm path: re-renders only when the live
    // order actually moved, so repeat visits never flicker.
    async function refreshEloOrder() {
      const before = groupsSignature();
      const ok = await fetchEloOrder(10000);
      if (!ok || view !== "setup") return;
      if (groupsSignature() === before) return;
      const interacting = document.activeElement && $("groups").contains(document.activeElement);
      if (!interacting) {
        expanded.clear();
        renderGroups();
      }
    }
    function renderGroupsLoading() {
      $("group-count").textContent = "gathering groups…";
      $("empty").hidden = true;
      $("select-visible").disabled = true;
      $("groups").innerHTML = Array.from({ length: 6 }, () =>
        '<article class="group-card is-loading" aria-hidden="true"><div class="group-cover"></div><div class="group-content"><div class="shimmer-bar"></div><div class="shimmer-bar short"></div></div></article>',
      ).join("");
    }

    let selected = new Set(),
      mode = "idols",
      generation = "all",
      session = null,
      sorter = null,
      view = "setup";
    const key = "bias-club-session-v1",
      lineupKey = "bias-club-lineup-v1";

    const photo = (item, cls = "") =>
      `<img src="${escape(imageURL(item))}" alt="${escape(item.name)}" loading="lazy" decoding="async" referrerpolicy="no-referrer" class="${cls}">`;

    $("search").value = navigation.readQuery();

    function idsFor(g) {
      return mode === "idols" ? g.members.map((i) => i.id) : g.gen?.length && g.photo ? [g.photo.id] : [];
    }
    function matchesGroupSearch(g, query) {
      const normalizedQuery = resolve(query);
      return g.name.toLowerCase().includes(query) ||
        (normalizedQuery.length > 0 && resolve(g.name).includes(normalizedQuery));
    }
    function visibleGroups() {
      const query = $("search").value.trim().toLowerCase();
      return groups.filter(
        (g) =>
          idsFor(g).length &&
          (generation === "all" ||
            (generation === "selected"
              ? idsFor(g).some((id) => selected.has(id))
              : g.gen?.includes(generation) ||
                g.members.some((m) => (m.gen || []).includes(generation)))) &&
          (!query ||
            matchesGroupSearch(g, query) ||
            (mode === "idols" && g.members.some((m) => m.name.toLowerCase().includes(query)))),
      );
    }
    const expanded = new Set();
    function summaryText(count, total) {
      return `${count ? `${count} / ` : ""}${total} member${total === 1 ? "" : "s"}${count ? " selected" : ""}`;
    }
    function allVisibleSelected(visible) {
      const list = visible ?? visibleGroups();
      if (!list.length) return false;
      return list.every((g) => idsFor(g).every((id) => selected.has(id)));
    }
    function updateSelectVisibleLabel() {
      const button = $("select-visible");
      if (!button) return;
      const visible = visibleGroups();
      if (!visible.length) {
        button.textContent = "Select shown";
        button.setAttribute?.("aria-pressed", "false");
        return;
      }
      const allSelected = allVisibleSelected(visible);
      button.textContent = allSelected ? "Unselect shown" : "Select shown";
      button.setAttribute?.("aria-pressed", String(allSelected));
    }
    function renderGroups() {
      const visible = visibleGroups();
      $("group-count").textContent = `${visible.length} groups & soloists`;
      $("empty").hidden = !!visible.length;
      $("select-visible").disabled = !visible.length;
      updateSelectVisibleLabel();
      $("groups").innerHTML = visible
        .map((g) => {
          const index = groups.indexOf(g),
            ids = idsFor(g),
            count = ids.filter((id) => selected.has(id)).length;
          const query = $("search").value.trim().toLowerCase();
          const matchedMember = query && !matchesGroupSearch(g, query);
          const open = expanded.has(index) || matchedMember;
          return `<article data-card="${index}" class="group-card ${
            count ? "has-selection" : ""
          }"><div class="group-cover">${photo(g.photo || g.members[0])}<label class="group-check"><input type="checkbox" data-group-check="${index}" aria-label="Select all ${escape(
            g.name,
          )}" ${count === ids.length ? "checked" : ""}></label></div><div class="group-content"><div class="group-title"><strong>${escape(
            g.name,
          )}</strong><span class="generation">${g.gen?.[0]?.replace("gen", "GEN ") || "SOLO"}</span></div>${
            mode === "idols"
              ? `<details data-group="${index}" ${open ? "open" : ""}><summary>${summaryText(
                  count,
                  ids.length,
                )}</summary><div class="members">${g.members
                  .map(
                    (m) =>
                      `<label><input type="checkbox" data-member="${m.id}" ${
                        selected.has(m.id) ? "checked" : ""
                      }>${escape(shortName(m))}</label>`,
                  )
                  .join("")}</div></details>`
              : `<div class="fine">${count ? "Selected" : "Not selected"}</div>`
          }</div></article>`;
        })
        .join("");
      document.querySelectorAll("[data-group-check]").forEach((input) => {
        const ids = idsFor(groups[Number(input.dataset.groupCheck)]),
          count = ids.filter((id) => selected.has(id)).length;
        input.indeterminate = count > 0 && count < ids.length;
      });
    }
    function syncSelectionUI(changed) {
      if (generation === "selected") {
        renderGroups();
        return;
      }
      const changedSet =
        changed === "all"
          ? "all"
          : new Set(
              Array.isArray(changed) ? changed : changed === null || changed === undefined ? [] : [changed],
            );
      try {
        const cards = document.querySelectorAll("[data-card]");
        if (!cards || !cards.length) return;
        cards.forEach((card) => {
          const index = Number(card.dataset?.card ?? card.getAttribute?.("data-card"));
          if (!Number.isFinite(index)) return;
          const g = groups[index];
          if (!g) return;
          const ids = idsFor(g),
            count = ids.filter((id) => selected.has(id)).length;
          card.classList?.toggle?.("has-selection", count > 0);
          const check = card.querySelector?.("[data-group-check]");
          if (check) {
            const shouldCheck = ids.length > 0 && count === ids.length;
            if (check.checked !== shouldCheck) check.checked = shouldCheck;
            check.indeterminate = count > 0 && count < ids.length;
          }
          const summary = card.querySelector?.("summary");
          if (summary && mode === "idols") {
            const next = summaryText(count, ids.length);
            if (summary.textContent !== next) summary.textContent = next;
          }
          const fine = card.querySelector?.(".group-content .fine");
          if (fine && mode !== "idols") {
            const next = count ? "Selected" : "Not selected";
            if (fine.textContent !== next) fine.textContent = next;
          }
          card.querySelectorAll?.("[data-member]")?.forEach?.((memberInput) => {
            const id = Number(memberInput.dataset?.member ?? memberInput.getAttribute?.("data-member"));
            const shouldCheck = selected.has(id);
            if (memberInput.checked !== shouldCheck) memberInput.checked = shouldCheck;
          });
          if (mode === "idols" && (changedSet === "all" || changedSet.has(index))) {
            const details = card.querySelector?.("details");
            if (details) {
              const shouldOpen = expanded.has(index);
              if (details.open !== shouldOpen) details.open = shouldOpen;
            }
          }
        });
      } catch {
        /* fall through; sidebar + toggle below stay correct */
      }
      updateSelectVisibleLabel();
    }
    function updateSelection() {
      const n = selected.size,
        bound = BiasSorter.bound(n, adaptiveDefaults);
      $("selection-count").textContent = n;
      $("selection-unit").textContent = mode;
      $("selected-groups").innerHTML = groups
        .filter((g) => idsFor(g).some((id) => selected.has(id)))
        .map(
          (g) =>
            `<button data-remove="${groups.indexOf(g)}" aria-label="Remove ${escape(g.name)}">${escape(g.name)} ×</button>`,
        )
        .join("");
      $("estimate").textContent = n > 1 ? `up to ${bound}` : "—";
      $("duration").textContent = n > 1 ? `~${Math.max(1, Math.ceil((bound * 5) / 60))} min` : "—";
      $("start").disabled = n < 2;
      $("clear").hidden = !n;
      $("start-hint").textContent = n < 2 ? `Choose at least 2 ${mode} to start` : "Autosaves on this device";
      const ids = [...selected];
      const previous = read(lineupKey);
      // Rendering or reopening the page must not extend the saved selection.
      if (previous?.mode !== mode || JSON.stringify(previous?.ids) !== JSON.stringify(ids)) {
        write(lineupKey, { mode, ids, savedAt: Date.now() });
      }
    }
    function refresh() {
      renderGroups();
      updateSelection();
    }
    function handleSelectionChange(changed) {
      if (generation === "selected") renderGroups();
      else syncSelectionUI(changed);
      updateSelection();
      updateSelectVisibleLabel();
    }
    try {
      $("groups").addEventListener(
        "toggle",
        (event) => {
          const details = event.target?.closest?.("details[data-group]");
          if (!details) return;
          const index = Number(details.dataset.group);
          if (!Number.isFinite(index)) return;
          if (details.open) expanded.add(index);
          else expanded.delete(index);
        },
        true,
      );
    } catch {
      /* test DOM stub */
    }
    $("groups").addEventListener("click", (event) => {
      if (event.target.closest("details, input, label, button, a")) return;
      event.target.closest("[data-card]")?.querySelector("[data-group-check]")?.click();
    });
    $("groups").addEventListener("change", (event) => {
      const input = event.target;
      let changedIndex = null;
      if (input.dataset.groupCheck !== undefined) {
        changedIndex = Number(input.dataset.groupCheck);
        const g = groups[changedIndex];
        if (g) {
          idsFor(g).forEach((id) => (input.checked ? selected.add(id) : selected.delete(id)));
          if (mode === "idols") {
            if (input.checked) expanded.add(changedIndex);
            else expanded.delete(changedIndex);
          }
        }
      } else if (input.dataset.member) {
        const id = Number(input.dataset.member);
        input.checked ? selected.add(id) : selected.delete(id);
        const owner = groups.find((g) => g.members.some((m) => m.id === id));
        if (owner) {
          changedIndex = groups.indexOf(owner);
          const remaining = idsFor(owner).filter((mid) => selected.has(mid)).length;
          if (remaining > 0) expanded.add(changedIndex);
          else expanded.delete(changedIndex);
        }
      }
      handleSelectionChange(changedIndex);
    });
    $("selected-groups").addEventListener("click", (event) => {
      const button = event.target.closest("[data-remove]");
      if (button) {
        const index = Number(button.dataset.remove);
        idsFor(groups[index]).forEach((id) => selected.delete(id));
        expanded.delete(index);
        handleSelectionChange(index);
      }
    });
    let searchTimer = null,
      searchQueued = false;
    $("search").addEventListener("input", () => {
      navigation.write(navigation.filterUrl({ query: $("search").value }), { replace: true });
      if (searchTimer) {
        searchQueued = true;
        return;
      }
      renderGroups();
      searchTimer = setTimeout(() => {
        searchTimer = null;
        if (searchQueued) {
          searchQueued = false;
          renderGroups();
        }
      }, 120);
    });
    document.querySelectorAll("[data-gen]").forEach(
      (button) =>
        (button.onclick = () => {
          generation = button.dataset.gen;
          document.querySelectorAll("[data-gen]").forEach((b) => b.setAttribute("aria-pressed", b === button));
          renderGroups();
        }),
    );
    document.querySelectorAll("[data-mode]").forEach(
      (button) =>
        (button.onclick = () => {
          if (mode === button.dataset.mode) return;
          const active = groups.filter((g) => idsFor(g).some((id) => selected.has(id)));
          mode = button.dataset.mode;
          selected = new Set(active.flatMap(idsFor));
          document.querySelectorAll("[data-mode]").forEach((b) => b.setAttribute("aria-pressed", b === button));
          // The two modes order differently (peak vs average), so re-sort.
          // Indices shift, so drop expanded state before re-rendering.
          orderGroups();
          expanded.clear();
          writeOrderCache();
          refresh();
        }),
    );
    $("select-visible").onclick = () => {
      const visible = visibleGroups();
      if (!visible.length) return;
      const ids = visible.flatMap(idsFor);
      if (ids.every((id) => selected.has(id))) {
        ids.forEach((id) => selected.delete(id));
        visible.forEach((g) => expanded.delete(groups.indexOf(g)));
        handleSelectionChange(visible.map((g) => groups.indexOf(g)));
      } else {
        ids.forEach((id) => selected.add(id));
        handleSelectionChange(null);
      }
    };
    $("clear").onclick = () => {
      selected.clear();
      expanded.clear();
      handleSelectionChange("all");
    };
    function setView(next) {
      view = next;
      const shown = next === "verifying" ? "sorting" : next;
      ["setup", "sorting", "results"].forEach((id) => ($(id).hidden = id !== shown));
      ["setup", "sort", "results"].forEach((id) => {
        const active = id === (shown === "sorting" ? "sort" : shown);
        if (active) $("step-" + id).setAttribute("aria-current", "step");
        else $("step-" + id).removeAttribute("aria-current");
      });
      const verifying = next === "verifying";
      $("pause").hidden = verifying;
      $("verify-back").hidden = !verifying;
      window.scrollTo({ top: 0, behavior: "instant" });
      if (next !== "setup") $(shown).focus({ preventScroll: true });
    }
    // Browser-back support, kept deliberately tiny: view-level entries only
    // (never per pick), stack never deeper than setup → results → verifying.
    // Back never mutates votes; finishing replaces the dead sorting entry so
    // Back from results lands on setup instead of bouncing back to results.
    // In-app back buttons delegate to history.back() with a setView fallback
    // for direct loads with no prior entry.
    let pushedFromSetup = false;
    let pendingToast = "";
    // History is best-effort: real browsers always have window.history, but
    // minimal DOM stubs (and privacy modes) may not. Views must work either
    // way — Back just exits when tagging is unavailable.
    function histState() {
      try {
        return (window.history || history)?.state?.sorterView;
      } catch {
        return undefined;
      }
    }
    function goBack() {
      try {
        const h = window.history || history;
        if (typeof h.back === "function") h.back();
      } catch {
        /* no history to pop — caller falls back to setView */
      }
    }
    function tagView(next, action, url) {
      try {
        const h = window.history || history;
        const target = url || location.pathname + location.search;
        const state = { sorterView: next };
        if (action === "push" && typeof h.pushState === "function") h.pushState(state, "", target);
        else if (typeof h.replaceState === "function") h.replaceState(state, "", target);
      } catch {
        /* history unavailable — views still work, Back just exits */
      }
    }
    function goView(next, action = "none") {
      if (view !== next) setView(next);
      if (action === "push" || action === "replace") tagView(next, action);
    }
    if (typeof window.addEventListener === "function") window.addEventListener("popstate", (event) => {
      $("search").value = navigation.readQuery();
      if (view === "setup") renderGroups();
      const target = event.state?.sorterView;
      if (!target || target === view || !session || !sorter) return;
      if (target === "setup") {
        verify = null;
        pushedFromSetup = false;
        save();
        updateResumeBanner();
        setView("setup");
        return;
      }
      if (target === "sorting") {
        if (sorter.result) {
          goView("results", "replace");
          renderResults();
          return;
        }
        verify = null;
        pushedFromSetup = true;
        setView("sorting");
        renderBattle();
        return;
      }
      if (target === "results") {
        verify = null;
        if (!sorter.result) {
          if (session.choices) {
            setView("sorting");
            renderBattle();
          } else setView("setup");
          tagView(view, "replace");
          return;
        }
        setView("results");
        renderResults();
        if (pendingToast) {
          toast(pendingToast);
          pendingToast = "";
        }
        return;
      }
      if (target === "verifying") {
        if (!sorter.result) {
          setView(session.choices ? "sorting" : "setup");
          if (view === "sorting") renderBattle();
          tagView(view, "replace");
          return;
        }
        if (!verify) {
          buckets = baseBuckets();
          if (validVerifyState(session.verify, session.ids)) {
            verify = {
              pairs: session.verify.pairs,
              shown: session.verify.shown,
              picks: [...session.verify.picks],
              limit: session.verify.limit,
            };
          } else {
            const pairs = buildVerifyPairs();
            if (!pairs.length) {
              setView("results");
              renderResults();
              tagView("results", "replace");
              return;
            }
            verify = {
              pairs,
              shown: pairs.map(() => (Math.random() < 0.5 ? [0, 1] : [1, 0])),
              picks: [],
              ...(session.algorithm ? { limit: 10 } : {}),
            };
            session.verify = null;
          }
        }
        setView("verifying");
        renderVerifyBattle();
      }
    });
    function save() {
      session.savedAt = Date.now();
      write(key, session);
    }
    function updateResumeBanner(value = readSavedState(key, true)) {
      const valid = validSession(value);
      $("resume-banner").hidden = !valid;
      if (!valid) return;
      const completed = Number.isFinite(value.finished) && value.finished > 0;
      $("resume-message").textContent = completed
        ? `Your ranking is ready ♡ · ${value.ids.length} ${value.mode} ranked`
        : `Your ranking is in progress · ${value.choices.length} choices made`;
      $("resume").textContent = completed ? "View results →" : "Continue ranking →";
    }
    function validSession(value) {
      return (
        value &&
        value.version === dataSetVersion &&
        ["idols", "groups"].includes(value.mode) &&
        Array.isArray(value.ids) &&
        value.ids.length >= 2 &&
        value.ids.length <= catalog.length &&
        new Set(value.ids).size === value.ids.length &&
        value.ids.every((id) =>
          Number.isInteger(id) && byId.has(id) &&
          (value.mode !== "groups" || groupSortIds.has(id))
        ) &&
        Array.isArray(value.choices) &&
        BiasSorter.validAlgorithm(value.algorithm, value.ids) &&
        value.choices.length <= BiasSorter.bound(value.ids.length, value.algorithm) &&
        value.choices.every((c) => ["left", "right", "tie"].includes(c)) &&
        (!value.algorithm || (Array.isArray(value.matchups) &&
          value.matchups.length === value.choices.length &&
          value.matchups.every((pair) => Array.isArray(pair) && pair.length === 2 &&
            pair[0] !== pair[1] && pair.every((id) => value.ids.includes(id))))) &&
        (value.verifyRounds === undefined || (
          Array.isArray(value.verifyRounds) &&
          value.verifyRounds.length <= 25 &&
          value.verifyRounds.every((round) =>
            Array.isArray(round) &&
            round.length <= 40 &&
            round.every((p) =>
              p && Number.isInteger(p.a) && Number.isInteger(p.b) &&
              value.ids.includes(p.a) && value.ids.includes(p.b) &&
              (p.winner === "tie" || p.winner === p.a || p.winner === p.b)
            )
          )
        )) &&
        (value.verify === undefined || value.verify === null ||
          validVerifyState(value.verify, value.ids))
      );
    }
    function resume(value, historyAction = "push") {
      if (!validSession(value)) {
        toast("This session could not be loaded. Please start a new lineup.");
        return;
      }
      session = value;
      session.verifyRounds = session.verifyRounds || [];
      sorter = BiasSorter.replay(session.ids, session.choices, session.algorithm, session.matchups);
      showFull = false;
      save();
      goView(sorter.result ? "results" : "sorting", historyAction);
      renderBattle();
      return true;
    }
    $("start").onclick = () => {
      if (selected.size < 2) return;
      const ids = [...selected];
      for (let i = ids.length - 1; i > 0; i--) {
        const j = Math.floor(Math.random() * (i + 1));
        [ids[i], ids[j]] = [ids[j], ids[i]];
      }
      session = { ballotId: crypto.randomUUID(), version: dataSetVersion, mode, ids, algorithm: newAlgorithm(ids), matchups: [], choices: [], verifyRounds: [], verify: null, counted: 0, started: Date.now() };
      sorter = BiasSorter.create(ids, session.algorithm);
      showFull = false;
      save();
      goView("sorting", "push");
      pushedFromSetup = true;
      renderBattle();
    };
    // Submit the finished evidence, never clicks. Keep a stable ID and frozen
    // payload until acknowledged so retries cannot accidentally submit edits.
    const ballotsInFlight = new Set();
    let ballotRetryTimer = null;
    const ballotRetryDelays = [2000, 5000, 15000];
    async function submitBallot(attempt = 0) {
      const current = session;
      if (current.mode !== "idols" || !current.ballotId || current.ballotDone ||
          ballotsInFlight.has(current.ballotId)) return;
      if (!current.ballotPayload) {
        const replay = current.algorithm ? null : BiasSorter.create(current.ids);
        const comparisons = [];
        current.choices.forEach((choice, index) => {
          const pair = current.algorithm ? current.matchups[index] : replay.pair();
          if (!current.algorithm) replay.choose(choice);
          const a = byId.get(pair[0]);
          const b = byId.get(pair[1]);
          const aId = a.leaderboard_id || a.role_id;
          const bId = b.leaderboard_id || b.role_id;
          if (aId && bId && aId !== bId) comparisons.push([
            aId, bId, choice === "tie" ? 0.5 : choice === "left" ? 1 : 0,
          ]);
        });
        current.ballotPayload = { ballot_id: current.ballotId, comparisons };
        save();
      }
      if (!current.ballotPayload.comparisons.length) return;
      clearTimeout(ballotRetryTimer);
      ballotRetryTimer = null;
      ballotsInFlight.add(current.ballotId);
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 15000);
      let retryable = false;
      try {
        const response = await fetch("/api/sorter/ballot", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify(current.ballotPayload), signal: controller.signal,
        });
        if (!response.ok) {
          retryable = response.status === 408 || response.status === 429 || response.status >= 500;
          return;
        }
        const result = await response.json();
        retryable = ["unavailable", "rate_limited"].includes(result.reason);
        if (result.recorded || ["repeat_ballot", "repeat", "budget_exhausted"].includes(result.reason)) {
          current.ballotDone = true;
          current.counted = result.recorded ? result.comparisons : 0;
          if (session === current) {
            save();
            if (view === "results") renderResults();
          }
        }
      } catch {
        retryable = true; // Network failure, timeout, or interrupted response.
      } finally {
        clearTimeout(timeout);
        ballotsInFlight.delete(current.ballotId);
        if (retryable && attempt < ballotRetryDelays.length &&
            session === current && view === "results" && !current.ballotDone) {
          ballotRetryTimer = setTimeout(() => {
            ballotRetryTimer = null;
            if (session === current && view === "results" && !current.ballotDone) {
              submitBallot(attempt + 1);
            }
          }, ballotRetryDelays[attempt]);
        }
      }
    }
    function renderBattle() {
      if (sorter.result) {
        session.finished ||= Date.now();
        save();
        if (view !== "results") goView("results", "replace");
        renderResults();
        submitBallot();
        return;
      }
      const pair = sorter.pair();
      ["left", "right"].forEach((side, index) => {
        const item = byId.get(pair[index]);
        const button = $("pick-" + side);
        const duel = session.mode === "groups";
        button.classList.toggle("group-duel", duel);
        button.innerHTML = `${
          duel
            ? `<span class="card-backdrop" aria-hidden="true" style="background-image:url(&quot;${escape(
                imageURL(item),
              )}&quot;)"></span>`
            : ""
        }${photo(item)}<strong>${escape(shortName(item))}</strong><small>${escape(groupName(item))}</small>`;
        button.setAttribute("aria-label", `Choose ${item.name}`);
      });
      const bound = BiasSorter.bound(session.ids.length, session.algorithm);
      $("battle-label").textContent = `MATCHUP ${session.choices.length + 1} · ${session.ids.length} ${session.mode.toUpperCase()}`;
      $("progress-label").textContent = `${session.choices.length} choices made · at most ${Math.max(
        0,
        bound - session.choices.length,
      )} left`;
      $("progress").value = (session.choices.length / bound) * 100;
      $("progress").setAttribute("aria-label", "Choices made relative to maximum matchups");
      $("undo").disabled = !session.choices.length;
      // Snappy next round: warm the cache for whatever pair comes next.
      // Both branches are cheap (deduped by URL) since only one can occur.
      try {
        if (session.algorithm) {
          const next = sorter.coverage[sorter.comparisons + 1] ||
            sorter.selectPair(true) || [];
          next.forEach((id) => preload(byId.get(id)));
        } else {
          const probe = BiasSorter.replay(session.ids, [...session.choices, "left"]);
          const next = probe.result ? null : probe.pair();
          if (next) next.forEach((id) => preload(byId.get(id)));
        }
      } catch {
        /* preview is best-effort */
      }
    }
    function clearVerifyRounds(reason) {
      if (session.verifyRounds?.length || session.verify) {
        session.verifyRounds = [];
        session.verify = null;
        if (reason) toast(reason);
      }
    }
    function choose(choice, button) {
      if (view !== "sorting" || sorter.result) return;
      const pair = sorter.pair();
      if (button) burst(button);
      if (session.algorithm) session.matchups.push(pair);
      session.choices.push(choice);
      sorter.choose(choice);
      save();
      renderBattle();
    }
    $("pick-left").onclick = (event) =>
      (view === "verifying" ? verifyChoose : choose)("left", event.currentTarget);
    $("pick-right").onclick = (event) =>
      (view === "verifying" ? verifyChoose : choose)("right", event.currentTarget);
    $("tie").onclick = () => (view === "verifying" ? verifyChoose("tie") : choose("tie"));
    $("undo").onclick = () => {
      if (view === "verifying") return verifyUndo();
      if (view !== "sorting") return;
      if (!session.choices.length) return;
      clearVerifyRounds();
      session.choices.pop();
      if (session.algorithm) session.matchups.pop();
      delete session.finished;
      sorter = BiasSorter.replay(session.ids, session.choices, session.algorithm, session.matchups);
      save();
      renderBattle();
    };
    $("pause").onclick = () => {
      save();
      updateResumeBanner();
      if (pushedFromSetup && histState() === "sorting") {
        pushedFromSetup = false;
        goBack();
      } else {
        pushedFromSetup = false;
        goView("setup", "replace");
      }
    };
    $("resume").onclick = () => {
      const saved = readSavedState(key, true);
      updateResumeBanner(saved);
      if (resume(saved, "push")) pushedFromSetup = true;
    };
    let ranked = [];
    let buckets = [];
    // Display length only — defaults to Top 50 so the finished view is
    // byte-for-byte identical unless the user opts into the full list.
    let showFull = false;
    const VERIFY_SLICE = 10;
    const CHALLENGE_TOP = 8;
    function baseBuckets() {
      return BiasSorter.ranking(sorter, session.verifyRounds);
    }
    function pairKey(a, b) {
      return a < b ? `${a}:${b}` : `${b}:${a}`;
    }
    // Every pair that already met: the main sort (replayed) plus all banked
    // verify picks. Ties count as faced too.
    function facedPairKeys() {
      const seen = BiasSorter.facedPairs(session.ids, session.choices);
      for (const round of session.verifyRounds || []) {
        for (const p of round || []) seen.add(pairKey(p.a, p.b));
      }
      return seen;
    }
    // One combined round: neighbouring ranks that never met directly first,
    // then unseen round-robin pairs inside the current top 8. Either half
    // can catch a misplaced idol; together the top 8 ends up fully compared.
    function buildVerifyPairs() {
      if (!sorter || !sorter.result || !buckets.length || session.verifyRounds.length >= 25) return [];
      if (session.algorithm) {
        const pair = sorter.withReviews(session.verifyRounds).selectPair(true, true);
        return pair ? [pair] : [];
      }
      const rankOf = new Map();
      buckets.forEach((bucket, index) => bucket.forEach((id) => rankOf.set(id, index)));
      const flat = [];
      buckets.forEach((bucket) => bucket.forEach((id) => flat.push(id)));
      const slice = flat.slice(0, VERIFY_SLICE);
      const pairs = [];
      const seen = new Set();
      let i = 0;
      while (i + 1 < slice.length) {
        if (rankOf.get(slice[i]) === rankOf.get(slice[i + 1])) {
          i += 1;
          continue;
        }
        pairs.push([slice[i], slice[i + 1]]);
        seen.add(pairKey(slice[i], slice[i + 1]));
        i += 2;
      }
      const faced = facedPairKeys();
      const top = flat.slice(0, CHALLENGE_TOP);
      const extra = [];
      for (let a = 0; a < top.length; a++) {
        for (let b = a + 1; b < top.length; b++) {
          const key = pairKey(top[a], top[b]);
          if (faced.has(key) || seen.has(key)) continue;
          seen.add(key);
          extra.push([top[a], top[b]]);
        }
      }
      // Shuffle the deep pairs so rank order can't cue answers.
      for (let k = extra.length - 1; k > 0; k--) {
        const j = Math.floor(Math.random() * (k + 1));
        [extra[k], extra[j]] = [extra[j], extra[k]];
      }
      return pairs.concat(extra);
    }
    function renderResults() {
      ranked = [];
      buckets = baseBuckets();
      let rank = 1;
      buckets.forEach((bucket) => {
        bucket.forEach((id) => ranked.push({ id, rank }));
        rank += bucket.length;
      });
      const counted = session.counted || 0;
      const pending = session.verify?.picks?.length || 0;
      if (pending) {
        $("verify").hidden = false;
        $("verify").textContent =
          `Resume refining · ${(session.verify.limit || session.verify.pairs.length) - pending} left →`;
      } else {
        const fresh = buildVerifyPairs();
        $("verify").hidden = fresh.length === 0;
        if (fresh.length) $("verify").textContent = "Refine ranking →";
      }
      $("result-refine").hidden = $("verify").hidden;
      const appearance = $("ranking-appearance");
      const leaderboard = $("leaderboard-cta");
      const limit = Number($("result-images").value);
      const visibleRanking = showFull ? ranked : ranked.slice(0, 50);
      const featured = visibleRanking.slice(0, limit);
      const remaining = visibleRanking.slice(limit);
      const headingCount = showFull || ranked.length <= 50
        ? `${ranked.length} ${escape(session.mode)}`
        : `Top 50 of ${ranked.length}`;
      $("ranking").innerHTML =
        `<header class="ranking-heading"><h2>My ranking <span>♡</span></h2><span>${headingCount}</span></header>` +
        (featured.length
          ? `<div class="rank-highlights">${featured
              .map(({ id, rank }) => {
                const item = byId.get(id);
                return `<div class="rank-highlight" data-rank="${rank}"><div class="rank-photo">${photo(item)}<span class="rank-badge${rank <= 3 ? " rank-badge-top" : ""}">${rank}</span></div><strong>${escape(
                  shortName(item),
                )}</strong><small>${escape(groupName(item))}</small></div>`;
              })
              .join("")}</div>`
          : "") +
        (remaining.length
          ? `<div class="rank-list">${remaining
              .map(({ id, rank }) => {
                const item = byId.get(id);
                return `<div class="rank-row" data-rank="${rank}"><span class="rank-number${rank <= 3 ? " rank-number-top" : ""}">${rank}</span>${photo(item, "rank-thumbnail")}<div><strong>${escape(
                  shortName(item),
                )}</strong><small>${escape(groupName(item))}</small></div></div>`;
              })
              .join("")}</div>`
          : "") +
        '<div class="ranking-credit">bias sorter ♡</div>';
      $("ranking").querySelector(".ranking-heading").append(appearance);
      leaderboard.hidden = !counted;
      const more = $("ranking-more");
      const toggle = $("toggle-full");
      if (more && toggle) {
        more.hidden = ranked.length <= 50;
        toggle.setAttribute("aria-expanded", String(showFull));
        toggle.textContent = showFull ? "Show top 50 ↑" : `View all ${ranked.length} ↓`;
      }
    }
    $("result-images").onchange = renderResults;
    const toggleFull = $("toggle-full");
    if (toggleFull) {
      toggleFull.onclick = () => {
        showFull = !showFull;
        renderResults();
        if (showFull) $("ranking-more")?.scrollIntoView({ block: "nearest" });
      };
    }
    $("undo-final").onclick = () => {
      if (!session.choices.length) return;
      clearVerifyRounds("Verification cleared — ranking changed.");
      setView("sorting");
      $("undo").click();
      tagView("sorting", "replace");
    };
    let verify = null;
    function validVerifyState(value, ids) {
      return (
        !!value &&
        Array.isArray(value.pairs) && value.pairs.length && value.pairs.length <= 40 &&
        Array.isArray(value.shown) && value.shown.length === value.pairs.length &&
        Array.isArray(value.picks) && value.picks.length <= value.pairs.length &&
        value.pairs.every((pair) =>
          Array.isArray(pair) && pair.length === 2 && pair[0] !== pair[1] &&
          pair.every((id) => Number.isInteger(id) && ids.includes(id))
        ) &&
        value.shown.every((s) =>
          Array.isArray(s) && s.length === 2 && s[0] !== s[1] &&
          (s[0] === 0 || s[0] === 1) && (s[1] === 0 || s[1] === 1)
        ) &&
        value.picks.every((p, i) =>
          p && value.pairs[i][0] === p.a && value.pairs[i][1] === p.b &&
          (p.winner === "tie" || p.winner === p.a || p.winner === p.b)
        ) &&
        (value.limit === undefined || (value.limit === 10 &&
          value.pairs.length <= value.limit &&
          value.pairs.length === value.picks.length + 1))
      );
    }
    function startVerify() {
      if (view !== "results" || !sorter?.result) return;
      buckets = baseBuckets();
      if (validVerifyState(session.verify, session.ids)) {
        verify = {
          pairs: session.verify.pairs,
          shown: session.verify.shown,
          picks: [...session.verify.picks],
          limit: session.verify.limit,
        };
      } else {
        const pairs = buildVerifyPairs();
        if (!pairs.length) return;
        verify = {
          pairs,
          shown: pairs.map(() => (Math.random() < 0.5 ? [0, 1] : [1, 0])),
          picks: [],
          ...(session.algorithm ? { limit: 10 } : {}),
        };
        session.verify = null;
      }
      goView("verifying", "push");
      renderVerifyBattle();
    }
    function renderVerifyBattle() {
      const pos = verify.picks.length;
      const total = verify.limit || verify.pairs.length;
      const [aId, bId] = verify.pairs[pos];
      const ids = verify.shown[pos][0] === 0 ? [aId, bId] : [bId, aId];
      ["left", "right"].forEach((side, index) => {
        const item = byId.get(ids[index]);
        const button = $("pick-" + side);
        const duel = session.mode === "groups";
        button.classList.toggle("group-duel", duel);
        button.innerHTML = `${
          duel
            ? `<span class="card-backdrop" aria-hidden="true" style="background-image:url(&quot;${escape(
                imageURL(item),
              )}&quot;)"></span>`
            : ""
        }${photo(item)}<strong>${escape(shortName(item))}</strong><small>${escape(groupName(item))}</small>`;
        button.setAttribute("aria-label", `Double-check: choose ${item.name}`);
      });
      $("battle-label").textContent =
        `DOUBLE-CHECK ${pos + 1}/${total} · ${session.algorithm ? "FAVORITES & CHALLENGERS" : `TOP ${Math.min(VERIFY_SLICE, ranked.length)}`}`;
      $("progress-label").textContent = `${pos} of ${total} re-checks`;
      $("progress").value = (pos / total) * 100;
      $("undo").disabled = pos === 0;
      if (pos + 1 < verify.pairs.length) {
        preload(byId.get(verify.pairs[pos + 1][0]));
        preload(byId.get(verify.pairs[pos + 1][1]));
      }
    }
    function verifyChoose(choice, button) {
      if (view !== "verifying" || !verify) return;
      const pos = verify.picks.length;
      if (pos >= verify.pairs.length) return;
      const [aId, bId] = verify.pairs[pos];
      const leftId = verify.shown[pos][0] === 0 ? aId : bId;
      const rightId = verify.shown[pos][0] === 0 ? bId : aId;
      if (button) burst(button);
      verify.picks.push({
        a: aId,
        b: bId,
        winner: choice === "tie" ? "tie" : choice === "left" ? leftId : rightId,
      });
      if (verify.limit && verify.picks.length < verify.limit) {
        const model = sorter.withReviews([...(session.verifyRounds || []), verify.picks]);
        const next = model.selectPair(true, true);
        if (next) {
          verify.pairs.push(next);
          verify.shown.push(Math.random() < 0.5 ? [0, 1] : [1, 0]);
        }
      }
      if (verify.picks.length >= verify.pairs.length) finishVerify();
      else {
        session.verify = { pairs: verify.pairs, shown: verify.shown, picks: verify.picks,
          ...(verify.limit ? { limit: verify.limit } : {}) };
        save();
        renderVerifyBattle();
      }
    }
    function verifyUndo() {
      if (view !== "verifying" || !verify || !verify.picks.length) return;
      verify.picks.pop();
      if (verify.limit) {
        verify.pairs = verify.pairs.slice(0, verify.picks.length + 1);
        verify.shown = verify.shown.slice(0, verify.picks.length + 1);
      }
      session.verify = { ...verify };
      save();
      renderVerifyBattle();
    }
    function finishVerify() {
      const round = verify.picks;
      verify = null;
      session.verify = null;
      let moved = 0;
      if (round.length) {
        const before = new Map(ranked.map((r) => [r.id, r.rank]));
        session.verifyRounds = [...(session.verifyRounds || []), round];
        save();
        setView("results");
        renderResults();
        const after = new Map(ranked.map((r) => [r.id, r.rank]));
        moved = round.filter(
          (p) => p.winner !== "tie" && before.get(p.winner) !== after.get(p.winner),
        ).length;
      } else {
        save();
        setView("results");
        renderResults();
      }
      const message = moved ? `${moved} favorite${moved === 1 ? "" : "s"} moved ♡` : "Ranking checked ♡";
      // Pop the verifying entry so Back never lands on a spent round;
      // the popstate handler re-renders results and shows this toast.
      if (histState() === "verifying") {
        pendingToast = message;
        goBack();
      } else {
        goView("results", "replace");
        toast(message);
      }
    }
    $("verify").onclick = startVerify;
    $("verify-back").onclick = () => {
      // Progress stays saved — tapping the button resumes where you left off.
      if (verify && !verify.picks.length) session.verify = null;
      verify = null;
      save();
      if (histState() === "verifying") {
        goBack();
      } else {
        goView("results", "replace");
        renderResults();
      }
    };
    $("new-lineup").onclick = () => {
      mode = session.mode;
      selected = new Set(session.ids);
      document.querySelectorAll("[data-mode]").forEach((b) => b.setAttribute("aria-pressed", b.dataset.mode === mode));
      updateResumeBanner();
      refresh();
      // Open the lineup directly: restored results can have another results
      // entry behind them, so browser Back may leave this screen unchanged.
      pushedFromSetup = false;
      goView("setup", "replace");
    };
    function download(blob, filename) {
      const url = URL.createObjectURL(blob),
        a = document.createElement("a");
      a.href = url;
      a.download = filename;
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    }
    $("text-export").onclick = () =>
      download(
        new Blob(
          ["My bias list ♡\n\n" + ranked.map((r) => `${r.rank}. ${byId.get(r.id).name}`).join("\n")],
          { type: "text/plain" },
        ),
        "my-bias-list.txt",
      );
    async function copySessionLink(inProgress = false) {
      const url = `${location.origin}${location.pathname}#${inProgress ? "continue" : "ranking"}=${LZString.compressToEncodedURIComponent(
        JSON.stringify(session),
      )}`;
      try {
        await navigator.clipboard.writeText(url);
        toast(inProgress ? "Progress link copied · resume from this point anytime." : "Result link copied ♡");
      } catch {
        window.prompt(inProgress ? "Copy your progress link:" : "Copy your result link:", url);
      }
    }
    $("share").onclick = () => copySessionLink();
    $("continue-link").onclick = () => copySessionLink(true);
    $("download").onclick = async () => {
      $("download").disabled = true;
      $("download").textContent = "Saving image…";
      try {
        const canvas = await createRankingImage({
          entries: ranked.map(({ id, rank }) => ({
            rank,
            name: shortName(byId.get(id)),
            group: groupName(byId.get(id)),
            image: imageURL(byId.get(id)),
          })),
          photoCount: Number($("result-images").value),
          mode: session.mode,
        });
        const blob = await new Promise((resolve) => canvas.toBlob(resolve, "image/png"));
        if (!blob) throw new Error("Image creation failed");
        download(blob, "my-bias-ranking.png");
        toast("Image saved ♡");
      } catch {
        toast("Could not save the image. Try fewer photos or download text.");
      } finally {
        $("download").disabled = false;
        $("download").textContent = "Save image ↓";
      }
    };
    $("help").onclick = () => $("help-dialog").showModal();
    $("close-help").onclick = () => $("help-dialog").close();
    document.addEventListener("keydown", (event) => {
      if (
        $("help-dialog").open ||
        /INPUT|TEXTAREA|SELECT/.test(event.target.tagName) ||
        event.ctrlKey ||
        event.metaKey ||
        event.altKey ||
        event.repeat
      )
        return;
      if (view === "sorting" || view === "verifying") {
        const pick = view === "sorting" ? choose : verifyChoose;
        const actions = {
          ArrowLeft: () => pick("left", $("pick-left")),
          h: () => pick("left", $("pick-left")),
          ArrowRight: () => pick("right", $("pick-right")),
          l: () => pick("right", $("pick-right")),
          ArrowUp: () => pick("tie"),
          k: () => pick("tie"),
          ArrowDown: () =>
            view === "sorting" ? $("undo").click() : verifyUndo(),
          j: () => (view === "sorting" ? $("undo").click() : verifyUndo()),
        };
        if (actions[event.key]) {
          event.preventDefault();
          actions[event.key]();
        }
      } else if (view === "setup" && event.key === "/") {
        event.preventDefault();
        $("search").focus();
      }
    });
    const previous = readSavedState(lineupKey);
    if (previous && ["idols", "groups"].includes(previous.mode) && Array.isArray(previous.ids)) {
      mode = previous.mode;
      selected = new Set(
        previous.ids.filter(
          (id) => Number.isInteger(id) && byId.has(id) &&
            (mode === "idols" ? byId.get(id).kind === "idol" : groupSortIds.has(id)),
        ),
      );
    }
    document.querySelectorAll("[data-mode]").forEach((b) => b.setAttribute("aria-pressed", b.dataset.mode === mode));
    updateResumeBanner();
    if (applyCachedOrder()) {
      refresh();
      refreshEloOrder();
    } else {
      renderGroupsLoading();
      await fetchEloOrder(1500);
      refresh();
    }
    const sessionLink = location.hash.match(/^#(ranking|continue)=(.*)$/);
    if (sessionLink) {
      try {
        const keepHash = sessionLink[1] === "ranking" ? location.hash : "";
        if (resume(JSON.parse(LZString.decompressFromEncodedURIComponent(sessionLink[2])), "none")) {
          // Preserve the old URL contract: ranking links keep their hash so a
          // refresh re-imports the snapshot; continue links drop it so a
          // refresh keeps newer local choices instead. Either way the entry
          // is tagged so Back steps to the referrer, not into a dead view.
          tagView(view, "replace", location.pathname + location.search + keepHash);
        }
      } catch {
        toast("That session link could not be read. Your lineup is ready below.");
      }
    } else {
      // A finished ranking opens right here on this page — no hunting under
      // another tab. Back still steps to the lineup below.
      const saved = readSavedState(key, true);
      if (saved && Number.isFinite(saved.finished) && saved.finished > 0 && resume(saved, "push")) {
        pushedFromSetup = true;
      } else {
        tagView(view, "replace");
      }
    }
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
