/* Leaderboard ♡ — fetches ELO boards and renders podium + spotlight + list. */
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

  function renderIdols(board) {
    const hasBaseline = !!board.movement_baseline_date;
    const tops = board.entries.slice(0, 3);
    const gridFour = board.entries.slice(3, 7);
    const gridFive = board.entries.slice(7, 12);
    const rest = board.entries.slice(12);
    let html = "";
    if (tops.length) {
      const ordered = [tops[1], tops[0], tops[2]].filter(Boolean);
      html += '<div class="podium">';
      ordered.forEach((entry) => {
        html += podiumCard(entry, board.entries.indexOf(entry) + 1);
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
    const basis =
      scope === "global"
        ? `Based on ${board.vote_count.toLocaleString()} votes`
        : board.vote_count > 0
          ? `Based on ${board.vote_count.toLocaleString()} of your votes`
          : "Your votes will shape this board";
    const movement = hasBaseline ? ` · Movement since ${escape(board.movement_baseline_date)}` : "";
    const explain = " · ELO is the head-to-head score; ♡ counts matchups";
    html += `<p class="board-foot">${basis}${movement}${explain}</p>`;
    return html;
  }

  function groupCard(entry, index) {
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
    return `<div class="group-card"><div class="group-photo-wrap">${photo}<span class="mini-rank">#${index + 1}</span><div class="fan">${fans}</div></div><div class="group-info"><strong>${escape(entry.group_name)}</strong><small>${entry.member_count} members · top ${escape(tops)}</small><span class="group-stats">${eloPill(entry.elo)}${votesPill(entry.votes)}</span></div></div>`;
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
    let html = "";
    if (board.entries.length) {
      html += groupHero(board.entries[0]);
    }
    if (board.entries.length > 1) {
      html += '<div class="group-list">';
      board.entries.slice(1).forEach((entry, i) => {
        html += groupCard(entry, i + 1);
      });
      html += "</div>";
    }
    const basis =
      scope === "global"
        ? `Based on ${board.vote_count.toLocaleString()} votes`
        : board.vote_count > 0
          ? `Based on ${board.vote_count.toLocaleString()} of your votes`
          : "Your votes will shape this board";
    html += `<p class="board-foot">${basis} · Group ELO is the average of the top ${board.top_n} members · ♡ counts their matchups</p>`;
    return html;
  }

  function renderEmpty() {
    return `<div class="empty"><div class="big">♡</div><h2>No votes yet</h2><p>Rank some idols in the sorter and your personal board will appear here.</p><a href="/sorter">Start sorting →</a></div>`;
  }

  async function load() {
    const board = $("board");
    board.innerHTML = '<div class="loading">Gathering idols</div>';
    try {
      const response = await fetch(`/api/leaderboard?scope=${scope}&kind=${kind}`, {
        credentials: "same-origin",
      });
      if (!response.ok) throw new Error("board " + response.status);
      const data = await response.json();
      if (kind === "idols" && scope === "personal" && !data.entries.length) {
        board.innerHTML = renderEmpty();
        return;
      }
      if (kind === "groups" && !data.entries.length) {
        board.innerHTML = scope === "personal" ? renderEmpty() : '<div class="loading">No groups ranked yet.</div>';
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
