/* Bias sorter ♡ — hanni web port of kpopidolsorter/src/js/app.js.
 *
 * Differences from the original:
 * - lineup data comes from /static/sorter/catalog.json (kpopping overwrites
 *   baked in at build time) instead of inline data.js + photo-cache.js
 * - every non-tie matchup with two known idols is beaconed to
 *   POST /api/sorter/vote so the global/personal ELO boards stay live
 * - next-pair images are preloaded after each render for instant cards
 *
 * Everything else (merge-sort engine, autosave, undo, share links, keyboard)
 * behaves exactly like the original.
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
  // Vendored same-origin portraits first (kpopping/legacy hosts block hotlinks
  // behind Cloudflare; Imgur originals are the last resort). Discord-proxied
  // kpopping portraits win when harvested.
  const imageURL = (item) =>
    (item.role_id && embedPhotos[item.role_id]) || item.local || item.fallback;

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

  function logVote(leftItem, rightItem, choice, session) {
    if (choice === "tie") return; // ties never touch ELO
    const winner = choice === "left" ? leftItem : rightItem;
    const loser = choice === "left" ? rightItem : leftItem;
    if (!winner.role_id || !loser.role_id) return;
    session.counted = (session.counted || 0) + 1;
    const payload = JSON.stringify({
      winner_role_id: winner.role_id,
      loser_role_id: loser.role_id,
    });
    try {
      if (navigator.sendBeacon) {
        navigator.sendBeacon("/api/sorter/vote", new Blob([payload], { type: "application/json" }));
      } else {
        fetch("/api/sorter/vote", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: payload,
          keepalive: true,
        }).catch(() => {});
      }
    } catch {
      /* votes are best-effort; the ranking is what matters */
    }
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
    const favoriteOrder = [
      "NewJeans", "aespa", "IVE", "LE SSERAFIM", "TWICE",
      "BLACKPINK", "Red Velvet", "ITZY", "NMIXX", "ILLIT",
    ];
    groups.sort((a, b) => {
      const priority = (g) => {
        const i = favoriteOrder.findIndex((n) => n.toLowerCase() === g.key.toLowerCase());
        return i < 0 ? 99 : i;
      };
      return priority(a) - priority(b) || a.name.localeCompare(b.name);
    });
    // Live touch: reorder groups by their current global average-member ELO
    // (same numbers as the Groups leaderboard). Best-effort — the favorite
    // order above stays until this resolves, and we never reshuffle under
    // an active selection.
    applyEloOrder();

    function normName(value) {
      return (value || "").toLowerCase().replace(/[^a-z0-9]/g, "");
    }
    async function applyEloOrder() {
      try {
        const response = await fetch("/api/leaderboard?scope=global&kind=groups", {
          credentials: "same-origin",
        });
        if (!response.ok) return;
        const board = await response.json();
        if (!board || !Array.isArray(board.entries)) return;
        const aliases = { idle: "gidle", ohmygirl: "omg" };
        aliases[normName("Girls' Generation")] = normName("SNSD");
        const resolve = (value) => aliases[normName(value)] || normName(value);
        const order = new Map();
        board.entries.forEach((entry, index) => {
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
        if (view === "setup" && selected.size === 0) renderGroups();
      } catch {
        /* favorite order stands */
      }
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

    function idsFor(g) {
      return mode === "idols" ? g.members.map((i) => i.id) : g.photo ? [g.photo.id] : [];
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
            g.name.toLowerCase().includes(query) ||
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
          const matchedMember = query && !g.name.toLowerCase().includes(query);
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
        bound = BiasSorter.bound(n);
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
      write(lineupKey, { mode, ids: [...selected] });
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
      ["setup", "sorting", "results"].forEach((id) => ($(id).hidden = id !== next));
      ["setup", "sort", "results"].forEach((id) => {
        const active = id === (next === "sorting" ? "sort" : next);
        if (active) $("step-" + id).setAttribute("aria-current", "step");
        else $("step-" + id).removeAttribute("aria-current");
      });
      window.scrollTo({ top: 0, behavior: "instant" });
      if (next !== "setup") $(next).focus({ preventScroll: true });
    }
    function save() {
      write(key, session);
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
        value.ids.every((id) => Number.isInteger(id) && byId.has(id)) &&
        Array.isArray(value.choices) &&
        value.choices.length <= BiasSorter.bound(value.ids.length) &&
        value.choices.every((c) => ["left", "right", "tie"].includes(c))
      );
    }
    function resume(value) {
      if (!validSession(value)) {
        toast("This session could not be loaded. Please start a new lineup.");
        return;
      }
      session = value;
      sorter = BiasSorter.replay(session.ids, session.choices);
      setView(sorter.result ? "results" : "sorting");
      renderBattle();
    }
    $("start").onclick = () => {
      if (selected.size < 2) return;
      const ids = [...selected];
      for (let i = ids.length - 1; i > 0; i--) {
        const j = Math.floor(Math.random() * (i + 1));
        [ids[i], ids[j]] = [ids[j], ids[i]];
      }
      session = { version: dataSetVersion, mode, ids, choices: [], counted: 0, started: Date.now() };
      sorter = new BiasSorter(ids);
      save();
      setView("sorting");
      renderBattle();
    };
    function renderBattle() {
      if (sorter.result) {
        session.finished ||= Date.now();
        save();
        if (view !== "results") setView("results");
        renderResults();
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
      const bound = BiasSorter.bound(session.ids.length);
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
        const probe = BiasSorter.replay(session.ids, [...session.choices, "left"]);
        const next = probe.result ? null : probe.pair();
        if (next) {
          preload(byId.get(next[0]));
          preload(byId.get(next[1]));
        }
      } catch {
        /* preview is best-effort */
      }
    }
    function choose(choice, button) {
      if (view !== "sorting" || sorter.result) return;
      const pair = sorter.pair();
      if (button) burst(button);
      logVote(byId.get(pair[0]), byId.get(pair[1]), choice, session);
      session.choices.push(choice);
      sorter.choose(choice);
      save();
      renderBattle();
    }
    $("pick-left").onclick = (event) => choose("left", event.currentTarget);
    $("pick-right").onclick = (event) => choose("right", event.currentTarget);
    $("tie").onclick = () => choose("tie");
    $("undo").onclick = () => {
      if (!session.choices.length) return;
      session.choices.pop();
      delete session.finished;
      sorter = BiasSorter.replay(session.ids, session.choices);
      save();
      renderBattle();
    };
    $("pause").onclick = () => {
      save();
      $("resume-banner").hidden = false;
      setView("setup");
    };
    $("resume").onclick = () => resume(read(key));
    let ranked = [];
    function renderResults() {
      ranked = [];
      let rank = 1;
      sorter.result.forEach((bucket) => {
        bucket.forEach((id) => ranked.push({ id, rank }));
        rank += bucket.length;
      });
      const counted = session.counted || 0;
      $("result-meta").textContent =
        `${session.ids.length} ${session.mode} · ${session.choices.length} matchups` +
        (counted ? ` · ♡ ${counted} picks counted toward the leaderboard` : "");
      const limit = Number($("result-images").value);
      const featured = ranked.slice(0, limit);
      const remaining = ranked.slice(limit);
      $("ranking").innerHTML =
        `<header class="ranking-heading"><h2>My ranking <span>♡</span></h2><span>${ranked.length} ${escape(
          session.mode,
        )}</span></header>` +
        (featured.length
          ? `<div class="rank-highlights">${featured
              .map(({ id, rank }) => {
                const item = byId.get(id);
                return `<div class="rank-highlight"><div class="rank-photo">${photo(item)}<span class="rank-badge">${rank}</span></div><strong>${escape(
                  shortName(item),
                )}</strong><small>${escape(groupName(item))}</small></div>`;
              })
              .join("")}</div>`
          : "") +
        (remaining.length
          ? `<div class="rank-list">${remaining
              .map(({ id, rank }) => {
                const item = byId.get(id);
                return `<div class="rank-row"><span class="rank-number">${rank}</span><div><strong>${escape(
                  shortName(item),
                )}</strong><small>${escape(groupName(item))}</small></div></div>`;
              })
              .join("")}</div>`
          : "") +
        '<div class="ranking-credit">bias sorter ♡</div>';
      $("leaderboard-cta").hidden = !counted;
    }
    $("result-images").onchange = renderResults;
    $("undo-final").onclick = () => {
      if (!session.choices.length) return;
      setView("sorting");
      $("undo").click();
    };
    $("new-lineup").onclick = () => {
      mode = session.mode;
      selected = new Set(session.ids);
      document.querySelectorAll("[data-mode]").forEach((b) => b.setAttribute("aria-pressed", b.dataset.mode === mode));
      history.replaceState(null, "", location.pathname);
      $("resume-banner").hidden = false;
      refresh();
      setView("setup");
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
    $("share").onclick = async () => {
      const url = `${location.origin}${location.pathname}#ranking=${LZString.compressToEncodedURIComponent(
        JSON.stringify(session),
      )}`;
      try {
        await navigator.clipboard.writeText(url);
        toast("Result link copied ♡");
      } catch {
        window.prompt("Copy your result link:", url);
      }
    };
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
      if (view === "sorting") {
        const actions = {
          ArrowLeft: () => choose("left", $("pick-left")),
          h: () => choose("left", $("pick-left")),
          ArrowRight: () => choose("right", $("pick-right")),
          l: () => choose("right", $("pick-right")),
          ArrowUp: () => choose("tie"),
          k: () => choose("tie"),
          ArrowDown: () => $("undo").click(),
          j: () => $("undo").click(),
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
    const previous = read(lineupKey);
    if (previous && ["idols", "groups"].includes(previous.mode) && Array.isArray(previous.ids)) {
      mode = previous.mode;
      selected = new Set(
        previous.ids.filter(
          (id) =>
            Number.isInteger(id) &&
            byId.has(id) &&
            (mode === "idols" ? byId.get(id).kind === "idol" : byId.get(id).kind === "group"),
        ),
      );
    }
    document.querySelectorAll("[data-mode]").forEach((b) => b.setAttribute("aria-pressed", b.dataset.mode === mode));
    $("resume-banner").hidden = !validSession(read(key));
    refresh();
    if (location.hash.startsWith("#ranking=")) {
      try {
        resume(JSON.parse(LZString.decompressFromEncodedURIComponent(location.hash.slice(9))));
      } catch {
        toast("That result link could not be read. Your lineup is ready below.");
      }
    }
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
