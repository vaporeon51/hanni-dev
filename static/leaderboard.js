/* Leaderboard ♡ — global ELO board.
 *
 * Fetches the server ELO board. Your own ranking lives on the sorter page;
 * finishing there is what moves these shared boards.
 */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
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

  function votesPill(votes, wins) {
    const full = (Number(votes) || 0).toLocaleString();
    if (wins == null) {
      return `<span class="row-votes" title="${full} recorded matchups">♡ ${formatVotes(votes)}</span>`;
    }
    const rate = votes > 0 ? `${Math.round(100 * wins / votes)}%` : "—";
    const label = votes > 0 ? `${rate} win rate` : "No matchups yet";
    const detail = `${Number(wins).toLocaleString()} wins · ${full} matchups`;
    return `<button type="button" class="row-votes win-rate-trigger" data-rate="${escape(label)}" data-detail="${escape(detail)}" aria-label="${escape(`${label}. ${detail}.`)}">♡ ${formatVotes(wins)}</button>`;
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
    return `<div class="podium-card${rank === 1 ? " first" : ""}" data-rank="${rank}"><div class="podium-photo">${imgTag(
      entry,
      "",
    )}<span class="podium-rank">#${rank}</span></div><strong>${escape(
      entry.member_name,
    )}</strong><small>${escape(entry.group_name || "")}</small><div class="podium-stats">${eloPill(
      entry.elo,
    )}${votesPill(entry.votes, entry.wins)}</div></div>`;
  }

  function miniCard(entry) {
    return `<div class="mini-card"><div class="mini-photo">${imgTag(
      entry,
      "",
    )}<span class="mini-rank">#${entry.rank}</span></div><strong>${escape(
      entry.member_name,
    )}</strong><small>${escape(entry.group_name || "")}</small><div class="mini-stats">${eloPill(
      entry.elo,
    )}${votesPill(entry.votes, entry.wins)}</div></div>`;
  }

  function listRow(entry, hasBaseline) {
    return `<div class="row"><span class="row-rank">#${entry.rank}</span>${imgTag(
      entry,
      "row-thumb",
    )}<div class="row-names"><strong>${escape(entry.member_name)}</strong><small>${escape(
      entry.group_name || "",
    )}</small></div>${movementPill(entry, hasBaseline)}${eloPill(entry.elo)}${votesPill(
      entry.votes, entry.wins,
    )}</div>`;
  }

  const tip = document.createElement("div");
  tip.id = "win-rate-tooltip";
  tip.className = "win-rate-tooltip";
  tip.setAttribute("role", "tooltip");
  tip.hidden = true;
  document.body.append(tip);
  let tipTrigger = null;

  function hideTip() {
    if (tipTrigger) tipTrigger.removeAttribute("aria-describedby");
    tipTrigger = null;
    tip.hidden = true;
  }

  function showTip(trigger) {
    hideTip();
    tipTrigger = trigger;
    trigger.setAttribute("aria-describedby", tip.id);
    tip.innerHTML = `<strong>${escape(trigger.dataset.rate)}</strong><span>${escape(trigger.dataset.detail)}</span>`;
    tip.hidden = false;
    const rect = trigger.getBoundingClientRect();
    const left = Math.max(8, Math.min(rect.left + rect.width / 2 - tip.offsetWidth / 2, window.innerWidth - tip.offsetWidth - 8));
    const above = rect.top - tip.offsetHeight - 8;
    tip.style.left = `${left}px`;
    tip.style.top = `${above >= 8 ? above : rect.bottom + 8}px`;
  }

  $("board").addEventListener("pointerover", (event) => {
    const trigger = event.target.closest(".win-rate-trigger");
    if (trigger && event.pointerType !== "touch") showTip(trigger);
  });
  $("board").addEventListener("pointerout", (event) => {
    if (event.target.closest(".win-rate-trigger") && !event.target.contains(event.relatedTarget)) hideTip();
  });
  $("board").addEventListener("focusin", (event) => {
    const trigger = event.target.closest(".win-rate-trigger");
    if (trigger) showTip(trigger);
  });
  $("board").addEventListener("focusout", hideTip);
  document.addEventListener("click", (event) => {
    const trigger = event.target.closest(".win-rate-trigger");
    if (trigger) showTip(trigger);
    else hideTip();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") hideTip();
  });
  window.addEventListener("scroll", hideTip, true);
  window.addEventListener("resize", hideTip);

  function renderIdols(board) {
    const hasBaseline = !!board.movement_baseline_date;
    const ranked = board.entries.filter((e) => !e.provisional);
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
    const basis = `Based on ${board.vote_count.toLocaleString()} recorded matchups`;
    const movement = hasBaseline ? ` · Movement since ${escape(board.movement_baseline_date)}` : "";
    const explain = " · ELO reflects head-to-head preferences · ♡ counts wins";
    html += `<p class="board-foot">${basis}${movement}${explain}</p>`;
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
    const basis = `Based on ${board.vote_count.toLocaleString()} recorded matchups`;
    html += `<p class="board-foot">${basis} · Group rankings cover the original member catalog; ELO averages the top ${board.top_n} scores · ♡ counts their matchups</p>`;
    return html;
  }

  async function load() {
    const board = $("board");
    hideTip();
    board.innerHTML = '<div class="loading">Gathering idols</div>';
    try {
      const response = await fetch(`/api/leaderboard?kind=${kind}&include_provisional=false`, {
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
    document.querySelectorAll("[data-kind]").forEach((b) =>
      b.setAttribute("aria-pressed", String(b.dataset.kind === kind)),
    );
  }

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
