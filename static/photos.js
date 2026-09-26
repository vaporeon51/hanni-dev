/* Idol photo wall ♡ — every sorter portrait in one grid.
 *
 * Resolution mirrors the sorter exactly (saved file, then Discord copy,
 * then the original address) so this doubles as a curation checklist:
 * the dot on each card shows where its photo comes from.
 */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);

  const escape = (value) =>
    String(value ?? "").replace(
      /[&<>"']/g,
      (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]),
    );

  // Same order as the sorter: saved file, Discord copy, original address.
  function resolve(item, embeds) {
    if (item.local) return { url: item.local, kind: "saved" };
    if (item.role_id && embeds[item.role_id]) return { url: embeds[item.role_id], kind: "discord" };
    if (item.fallback) return { url: item.fallback, kind: "remote" };
    return { url: null, kind: "missing" };
  }

  function card(item, resolved) {
    const name = item.short || item.name;
    const group = item.group || (item.groups || []).join(", ") || "Solo";
    const img = resolved.url
      ? `<img src="${escape(resolved.url)}" alt="${escape(name)}" loading="lazy" decoding="async" referrerpolicy="no-referrer" onerror="this.closest('.photo-card').classList.add('broken');window.__photoBroken=(window.__photoBroken||0)+1;">`
      : "";
    return `<div class="photo-card" data-search="${escape((name + " " + group).toLowerCase())}"><div class="photo-wrap">${img}</div><strong><span class="dot ${resolved.kind}" title="${resolved.kind}"></span>${escape(name)}</strong><small>${escape(group)}</small></div>`;
  }

  // Same lineup order as idol selection: groups by peak-member score from
  // the group board, anything off-board in alphabetical order at the end.
  const normKey = (value) => String(value || "").toLowerCase().replace(/[^a-z0-9]/g, "");
  function peakOrder(board) {
    const entries = [...((board && board.entries) || [])].sort(
      (a, b) => (b.peak_elo ?? b.elo) - (a.peak_elo ?? a.elo),
    );
    const order = new Map();
    entries.forEach((entry, index) => {
      const key = normKey(entry.group_name);
      if (!order.has(key)) order.set(key, index);
    });
    return order;
  }

  function render(items, embeds, order) {
    const wall = $("wall");
    const query = $("search").value.trim().toLowerCase();
    const matches = (item) =>
      !query ||
      `${item.short || item.name} ${item.group || ""} ${(item.groups || []).join(" ")}`.toLowerCase().includes(query);
    const groups = new Map();
    items.forEach((item) => {
      if (!matches(item)) return;
      const key = item.group || "Solo";
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(item);
    });
    const rankOf = (name) => {
      const rank = order.get(normKey(name));
      return rank === undefined ? Infinity : rank;
    };
    const ordered = [...groups.entries()].sort(
      (a, b) => rankOf(a[0]) - rankOf(b[0]) || (a[0] === "Solo" ? 1 : b[0] === "Solo" ? -1 : a[0].localeCompare(b[0])),
    );
    wall.innerHTML = ordered.length
      ? ordered
          .map(
            ([name, members]) =>
              `<section class="photo-group"><h2>${escape(name)} <span>· ${members.length}</span></h2><div class="photo-grid">` +
              members.map((item) => card(item, resolve(item, embeds))).join("") +
              `</div></section>`,
          )
          .join("")
      : '<div class="empty">No matches. Try another name.</div>';
    const counts = { saved: 0, discord: 0, remote: 0, missing: 0 };
    items.forEach((item) => {
      counts[resolve(item, embeds).kind] += 1;
    });
    const shown = ordered.reduce((n, [, members]) => n + members.length, 0);
    $("stats").textContent =
      `${items.length} portraits · ${counts.saved} saved · ${counts.discord} discord · ${counts.remote} original · ${counts.missing} missing` +
      (shown !== items.length ? ` · showing ${shown}` : "");
  }

  async function boot() {
    const wall = $("wall");
    wall.innerHTML = '<div class="loading">Gathering photos</div>';
    try {
      const [catalogResponse, embedsResponse, boardResponse] = await Promise.all([
        fetch("/static/sorter/catalog.json", { credentials: "same-origin" }),
        fetch("/static/sorter/embed-photos.json", { credentials: "same-origin" }),
        fetch("/api/leaderboard?kind=groups&limit=200", { credentials: "same-origin" }).catch(() => null),
      ]);
      if (!catalogResponse.ok) throw new Error("catalog " + catalogResponse.status);
      const catalog = await catalogResponse.json();
      const embeds = embedsResponse.ok ? ((await embedsResponse.json()).by_role || {}) : {};
      let board = null;
      try {
        board = boardResponse && boardResponse.ok ? await boardResponse.json() : null;
      } catch {
        board = null;
      }
      const order = peakOrder(board);
      const items = (catalog.entries || [])
        .filter((item) => item.kind !== "group")
        .sort((a, b) => (a.short || a.name).localeCompare(b.short || b.name));
      $("search").addEventListener("input", () => render(items, embeds, order));
      render(items, embeds, order);
    } catch {
      wall.innerHTML = '<div class="loading">Could not load photos. Please refresh to try again.</div>';
    }
  }

  boot();
})();
