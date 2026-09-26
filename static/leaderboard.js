/* Leaderboard ♡ — global ELO board plus your own sorter ranking.
 *
 * Global tabs fetch the server ELO board. The Mine tab renders your last
 * sorter session straight from localStorage, using its versioned engine and
 * completed refinement rounds to match the sorter results page.
 */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  let scope = "global";
  let kind = "idols";

  const escape = (value) =>
    String(value ?? "").replace(
      /[&<>"']/g,
      (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]),
    );

  function formatVotes(n) {
    n = Number(n) || 0;
    if (n < 1000) return `${n}`;
    if (n < 1000000) {
      const k = (n / 1000).toFixed(1).replace(/\.0$/, "");
      return `${k}k`;
    }
    const m = (n / 1000000).toFixed(1).replace(/\.0$/, "");
    return `${m}M`;
  }

  function eloPill(elo) {
    return `<span class="row-elo"><span class="elo-tag">ELO</span><span class="elo-num">${elo}</span></span>`;
  }

  function votesPill(votes) {
    const full = (Number(votes) || 0).toLocaleString();
    return `<span class="row-votes" title="${full} votes">♡ ${formatVotes(votes)}</span>`;
  }

  function movementPill(entry, hasBaseline) {
    if (!hasBaseline) return "";
    let cls = "move-same";
    let text = "–";
    if (entry.previous_rank == null) {
      cls = "move-new";
      text = "NEW";
    } else if (entry.previous_rank > entry.rank) {
      cls = "move-up";
      text = `▲${entry.previous_rank - entry.rank}`;
    } else if (entry.previous_rank < entry.rank) {
      cls = "move-down";
      text = `▼${entry.rank - entry.previous_rank}`;
    }
    return `<span class="row-move ${cls}">${text}</span>`;
  }

  function fallbackImage() {
    return (
      "data:image/svg+xml," +
      encodeURIComponent(
        '<svg xmlns="http://www.w3.org/2000/svg" width="88" height="96" viewBox="0 0 88 96"><rect width="88" height="96" fill="#fbe8f0"/><text x="44" y="62" text-anchor="middle" font-size="36" fill="#b84d79">♡</text></svg>',
      )
    );
  }

  function imgTag(entry, cls, extra = "") {
    return `<img src="${escape(entry.image_url)}" alt="${escape(
      entry.member_name,
    )}" loading="lazy" decoding="async" referrerpolicy="no-referrer" onerror="this.onerror=null;this.src='${fallbackImage()}'" class="${cls}"${extra}>`;
  }

  function podiumCard(entry, rank) {
    return `<div class="podium-card${rank === 1 ? " first" : ""}"><div class="podium-photo">${imgTag(
      entry,
      "",
    )}<span class="podium-rank">#${rank}</span></div><strong>${escape(
      entry.member_name,
    )}</strong><small>${escape(entry.group_name || "")}</small><div class="podium-stats">${eloPill(
      entry.elo,
    )}</div><div class="podium-votes">${votesPill(entry.votes)}</div></div>`;
  }

  function miniCard(entry) {
    return `<div class="mini-card"><div class="mini-photo">${imgTag(
      entry,
      "",
    )}<span class="mini-rank">#${entry.rank}</span></div><strong>${escape(
      entry.member_name,
    )}</strong><small>${escape(entry.group_name || "")}</small><div class="mini-stats">${eloPill(
      entry.elo,
    )}</div><div class="mini-votes">${votesPill(entry.votes)}</div></div>`;
  }

  function listRow(entry, hasBaseline) {
    return `<div class="row"><span class="row-rank">#${entry.rank}</span>${imgTag(
      entry,
      "row-thumb",
    )}<div class="row-names"><strong>${escape(entry.member_name)}</strong><small>${escape(
      entry.group_name || "",
    )}</small></div>${movementPill(entry, hasBaseline)}${eloPill(entry.elo)}${votesPill(
      entry.votes,
    )}</div>`;
  }

  function freshIdolCard(entry) {
    return `<div class="mini-card fresh"><div class="mini-photo">${imgTag(
      entry,
      "",
    )}<span class="mini-rank fresh-tag">NEW</span></div><strong>${escape(
      entry.member_name,
    )}</strong><small>${escape(entry.group_name || "")}</small><div class="mini-stats">${eloPill(
      entry.elo,
    )}</div><div class="mini-votes">${votesPill(entry.votes)}</div></div>`;
  }

  function freshStrip(cards, noun) {
    if (!cards.length) return "";
    return `<h3 class="fresh-head">Fresh faces ♡</h3><p class="fresh-sub">Not enough ${noun} matchups to rank yet — sort them to weigh in.</p><div class="mini-grid mini-grid-5">${cards}</div>`;
  }

  function renderIdols(board) {
    const hasBaseline = !!board.movement_baseline_date;
    const ranked = board.entries.filter((e) => !e.provisional);
    const fresh = board.entries.filter((e) => e.provisional);
    const tops = ranked.slice(0, 3);
    const gridFour = ranked.slice(3, 7);
    const gridFive = ranked.slice(7, 12);
    const rest = ranked.slice(12);
    let html = "";
    if (tops.length) {
      const ordered = [tops[1], tops[0], tops[2]].filter(Boolean);
      html += '<div class="podium">';
      ordered.forEach((entry) => {
        html += podiumCard(entry, entry.rank);
      });
      html += "</div>";
    }
    if (gridFour.length) {
      html += '<div class="mini-grid mini-grid-4">';
      gridFour.forEach((entry) => {
        html += miniCard(entry);
      });
      html += "</div>";
    }
    if (gridFive.length) {
      html += '<div class="mini-grid mini-grid-5">';
      gridFive.forEach((entry) => {
        html += miniCard(entry);
      });
      html += "</div>";
    }
    if (rest.length) {
      html += '<div class="rows">';
      rest.forEach((entry) => {
        html += listRow(entry, hasBaseline);
      });
      html += "</div>";
    }
    html += freshStrip(fresh.map(freshIdolCard).join(""), "idol");
    const basis =
      scope === "global"
        ? `Based on ${board.vote_count.toLocaleString()} votes`
        : board.vote_count > 0
          ? `Based on ${board.vote_count.toLocaleString()} of your votes`
          : "Your votes will shape this board";
    const movement = hasBaseline ? ` · Movement since ${escape(board.movement_baseline_date)}` : "";
    const explain = " · ELO is the head-to-head score; ♡ counts matchups";
    const freshNote = fresh.length ? " · fresh faces need more matchups to join the ranks" : "";
    html += `<p class="board-foot">${basis}${movement}${explain}${freshNote}</p>`;
    return html;
  }

  function groupCard(entry) {
    const members = entry.top_members || [];
    const fans = members
      .slice(0, 3)
      .map((member) =>
        member.image_url
          ? `<img src="${escape(member.image_url)}" alt="${escape(member.name)}" title="${escape(member.name)}" loading="lazy" decoding="async" referrerpolicy="no-referrer" onerror="this.remove()" class="fan-avatar">`
          : "",
      )
      .join("");
    const tops = members
      .slice(0, 3)
      .map((member) => member.name)
      .join(", ");
    const photo = entry.image_url
      ? `<img src="${escape(entry.image_url)}" alt="${escape(entry.group_name)}" loading="lazy" decoding="async" referrerpolicy="no-referrer" onerror="this.onerror=null;this.src='${fallbackImage()}'" class="group-photo">`
      : "";
    return `<div class="group-card"><div class="group-photo-wrap">${photo}<span class="mini-rank">#${entry.rank}</span><div class="fan">${fans}</div></div><div class="group-info"><strong>${escape(entry.group_name)}</strong><small>${entry.member_count} members · top ${escape(tops)}</small><span class="group-stats">${eloPill(entry.elo)}${votesPill(entry.votes)}</span></div></div>`;
  }

  function freshGroupCard(entry) {
    return `<div class="group-card fresh"><div class="group-photo-wrap"><span class="mini-rank fresh-tag">NEW</span></div><div class="group-info"><strong>${escape(entry.group_name)}</strong><small>${entry.member_count} members</small><span class="group-stats">${eloPill(entry.elo)}${votesPill(entry.votes)}</span></div></div>`;
  }

  function groupHero(entry) {
    const members = entry.top_members || [];
    const fans = members
      .slice(0, 3)
      .map((member) =>
        member.image_url
          ? `<img src="${escape(member.image_url)}" alt="${escape(member.name)}" title="${escape(member.name)}" loading="lazy" decoding="async" referrerpolicy="no-referrer" onerror="this.remove()" class="fan-avatar">`
          : "",
      )
      .join("");
    const tops = members
      .slice(0, 3)
      .map((member) => member.name)
      .join(", ");
    const photo = entry.image_url
      ? `<img src="${escape(entry.image_url)}" alt="${escape(entry.group_name)}" loading="lazy" decoding="async" referrerpolicy="no-referrer" onerror="this.onerror=null;this.src='${fallbackImage()}'" class="group-photo">`
      : "";
    return `<div class="group-hero"><div class="group-photo-wrap hero-photo-wrap">${photo}<span class="hero-rank">#1</span><div class="fan">${fans}</div></div><div class="group-hero-info"><div class="hero-eyebrow">top group ♡</div><strong>${escape(entry.group_name)}</strong><small>${entry.member_count} members · top ${escape(tops)}</small><span class="group-stats">${eloPill(entry.elo)}${votesPill(entry.votes)}</span></div></div>`;
  }

  function renderGroups(board) {
    const ranked = board.entries.filter((e) => !e.provisional);
    const fresh = board.entries.filter((e) => e.provisional);
    let html = "";
    if (ranked.length) {
      html += groupHero(ranked[0]);
    }
    if (ranked.length > 1) {
      html += '<div class="group-list">';
      ranked.slice(1).forEach((entry) => {
        html += groupCard(entry);
      });
      html += "</div>";
    }
    html += freshStrip(fresh.map(freshGroupCard).join(""), "group");
    const basis =
      scope === "global"
        ? `Based on ${board.vote_count.toLocaleString()} votes`
        : board.vote_count > 0
          ? `Based on ${board.vote_count.toLocaleString()} of your votes`
          : "Your votes will shape this board";
    html += `<p class="board-foot">${basis} · Group ELO is the average of the top ${board.top_n} members · ♡ counts their matchups${fresh.length ? " · fresh faces need more matchups to join the ranks" : ""}</p>`;
    return html;
  }

  function renderEmpty() {
    return `<div class="empty"><div class="big">♡</div><h2>No finished ranking yet</h2><p>Finish a sorter run and your ranking will show up here — exactly as the sorter called it.</p><a href="/sorter">Start sorting →</a></div>`;
  }

  function mineImg(item, cls) {
    return `<img src="${escape(item.img)}" alt="${escape(item.name)}" loading="lazy" decoding="async" referrerpolicy="no-referrer" onerror="this.onerror=null;this.src='${fallbackImage()}'" class="${cls}">`;
  }

  function minePodiumCard(item, rank) {
    return `<div class="podium-card${rank === 1 ? " first" : ""}"><div class="podium-photo">${mineImg(item, "")}<span class="podium-rank">#${rank}</span></div><strong>${escape(item.name)}</strong><small>${escape(item.group)}</small></div>`;
  }

  function mineMiniCard(item) {
    return `<div class="mini-card"><div class="mini-photo">${mineImg(item, "")}<span class="mini-rank">#${item.rank}</span></div><strong>${escape(item.name)}</strong><small>${escape(item.group)}</small></div>`;
  }

  function mineListRow(item) {
    return `<div class="row"><span class="row-rank">#${item.rank}</span>${mineImg(item, "row-thumb")}<div class="row-names"><strong>${escape(item.name)}</strong><small>${escape(item.group)}</small></div></div>`;
  }

  function renderMine(ranked, session) {
    const tops = ranked.slice(0, 3);
    const gridFour = ranked.slice(3, 7);
    const gridFive = ranked.slice(7, 12);
    const rest = ranked.slice(12);
    let html = "";
    if (tops.length) {
      const ordered = [tops[1], tops[0], tops[2]].filter(Boolean);
      html += '<div class="podium">';
      ordered.forEach((item) => {
        html += minePodiumCard(item, item.rank);
      });
      html += "</div>";
    }
    if (gridFour.length) {
      html += '<div class="mini-grid mini-grid-4">';
      gridFour.forEach((item) => {
        html += mineMiniCard(item);
      });
      html += "</div>";
    }
    if (gridFive.length) {
      html += '<div class="mini-grid mini-grid-5">';
      gridFive.forEach((item) => {
        html += mineMiniCard(item);
      });
      html += "</div>";
    }
    if (rest.length) {
      html += '<div class="rows">';
      rest.forEach((item) => {
        html += mineListRow(item);
      });
      html += "</div>";
    }
    html += `<p class="board-foot">your sorter ranking · ${session.ids.length} ${escape(session.mode)} · ${session.choices.length} matchups · <a href="/sorter">open in sorter →</a></p>`;
    return html;
  }

  function validMineSession(session) {
    return (
      !!session &&
      ["idols", "groups"].includes(session.mode) &&
      Array.isArray(session.ids) &&
      session.ids.length >= 2 &&
      session.ids.length <= 2000 &&
      new Set(session.ids).size === session.ids.length &&
      BiasSorter.validAlgorithm(session.algorithm, session.ids) &&
      Array.isArray(session.choices) &&
      session.choices.length <= BiasSorter.bound(session.ids.length, session.algorithm) &&
      session.choices.every((c) => ["left", "right", "tie"].includes(c)) &&
      (session.verifyRounds === undefined || (Array.isArray(session.verifyRounds) &&
        session.verifyRounds.length <= 25 && session.verifyRounds.every((round) =>
          Array.isArray(round) && round.length <= 40 && round.every((p) =>
            p && p.a !== p.b && session.ids.includes(p.a) && session.ids.includes(p.b) &&
            (p.winner === "tie" || p.winner === p.a || p.winner === p.b)))))
    );
  }

  async function loadMine() {
    const board = $("board");
    board.innerHTML = '<div class="loading">Finding your ranking</div>';
    if (typeof BiasSorter === "undefined") {
      board.innerHTML = '<div class="loading">Could not load the sorter engine. Please refresh to try again.</div>';
      return;
    }
    let session = null;
    try {
      session = JSON.parse(localStorage.getItem("bias-club-session-v1"));
    } catch {
      session = null;
    }
    if (!validMineSession(session)) {
      board.innerHTML = renderEmpty();
      return;
    }
    let catalog;
    let embeds = {};
    try {
      const [catalogResponse, embedsResponse] = await Promise.all([
        fetch("/static/sorter/catalog.json", { credentials: "same-origin" }),
        fetch("/static/sorter/embed-photos.json", { credentials: "same-origin" }),
      ]);
      if (!catalogResponse.ok) throw new Error("catalog " + catalogResponse.status);
      catalog = await catalogResponse.json();
      if (embedsResponse.ok) {
        const embedsData = await embedsResponse.json();
        embeds = (embedsData && embedsData.by_role) || {};
      }
    } catch {
      board.innerHTML = '<div class="loading">Could not load the idol catalog. Please refresh to try again.</div>';
      return;
    }
    if ((catalog.version || "2025-11-01") !== session.version) {
      board.innerHTML = renderEmpty();
      return;
    }
    const byId = new Map(catalog.entries.map((item) => [item.id, item]));
    if (!session.ids.every((id) => byId.has(id))) {
      board.innerHTML = renderEmpty();
      return;
    }
    let sorter = null;
    try {
      sorter = BiasSorter.replay(session.ids, session.choices, session.algorithm, session.matchups);
    } catch {
      board.innerHTML = renderEmpty();
      return;
    }
    if (!sorter.result) {
      board.innerHTML = `<div class="empty"><div class="big">♡</div><h2>Ranking in progress</h2><p>${session.choices.length} choices in — pick up where you left off.</p><a href="/sorter">Resume sorting →</a></div>`;
      return;
    }
    const imageURL = (item) =>
      item.local || (item.role_id && embeds[item.role_id]) || item.fallback;
    const ranked = [];
    let rank = 1;
    BiasSorter.ranking(sorter, session.verifyRounds).forEach((bucket) => {
      bucket.forEach((id) => {
        const item = byId.get(id);
        ranked.push({
          rank,
          name: item.short || item.name,
          group: item.group || "Group",
          img: imageURL(item),
        });
      });
      rank += bucket.length;
    });
    board.innerHTML = renderMine(ranked, session);
  }

  async function load() {
    if (scope === "personal") {
      loadMine();
      return;
    }
    const board = $("board");
    board.innerHTML = '<div class="loading">Gathering idols</div>';
    try {
      const response = await fetch(`/api/leaderboard?kind=${kind}`, {
        credentials: "same-origin",
      });
      if (!response.ok) throw new Error("board " + response.status);
      const data = await response.json();
      if (!data.entries.length) {
        board.innerHTML = '<div class="loading">No groups ranked yet.</div>';
        return;
      }
      board.innerHTML = kind === "idols" ? renderIdols(data) : renderGroups(data);
    } catch {
      board.innerHTML = '<div class="loading">Could not load the board. Please refresh to try again.</div>';
    }
  }

  function syncTabs() {
    document.querySelectorAll("[data-scope]").forEach((b) =>
      b.setAttribute("aria-pressed", String(b.dataset.scope === scope)),
    );
    document.querySelectorAll("[data-kind]").forEach((b) =>
      b.setAttribute("aria-pressed", String(b.dataset.kind === kind)),
    );
    const subtabs = document.querySelector(".subtabs");
    if (subtabs) subtabs.hidden = scope === "personal";
  }

  document.querySelectorAll("[data-scope]").forEach((button) => {
    button.addEventListener("click", () => {
      if (scope === button.dataset.scope) return;
      scope = button.dataset.scope;
      syncTabs();
      load();
    });
  });
  document.querySelectorAll("[data-kind]").forEach((button) => {
    button.addEventListener("click", () => {
      if (kind === button.dataset.kind) return;
      kind = button.dataset.kind;
      syncTabs();
      load();
    });
  });

  syncTabs();
  load();
})();
