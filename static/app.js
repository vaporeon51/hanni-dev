const ALLOWED_LIMITS = new Set([1, 15, 30]);
const ALLOWED_SORTS = new Set(["random", "latest", "oldest", "top"]);
const REVEAL_DELAY_MS = 2000;
const state = { items: [], revealTimer: null, revealToken: 0, visibleCount: 0 };

const $ = (id) => document.getElementById(id);

const videoPlaybackObserver = "IntersectionObserver" in window
  ? new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) {
          entry.target.play().catch(() => {});
        } else {
          entry.target.pause();
        }
      });
    }, { threshold: 0.25 })
  : null;

function setStatus(text) {
  const status = $("status");
  status.textContent = text;
  status.hidden = !text;
  status.closest(".feed-status").hidden = !text && $("reveal-progress").hidden;
}

function setRevealProgress(active) {
  const progress = $("reveal-progress");
  const statusContainer = progress.closest(".feed-status");
  progress.hidden = !active;
  progress.classList.remove("is-counting");
  if (active) {
    // Restart the two-second fill animation for each incoming card.
    void progress.offsetWidth;
    progress.classList.add("is-counting");
  }
  statusContainer.hidden = !active && $("status").hidden;
}

function setStopVisible(visible) {
  $("stop-feed").hidden = !visible;
}

function setSkipLatestVisible(visible) {
  $("skip-latest").hidden = !visible;
}

function setMoveTopVisible(visible) {
  $("move-top").hidden = !visible;
}

function setFeedOverlayFloating(floating) {
  $("status").closest(".feed-status").classList.toggle("is-floating", floating);
}

function cancelReveal() {
  if (state.revealTimer !== null) window.clearTimeout(state.revealTimer);
  state.revealTimer = null;
  state.revealToken += 1;
  setRevealProgress(false);
  setStopVisible(false);
}

function stopFeed() {
  if (state.revealTimer === null) return;
  const visibleCount = state.visibleCount;
  cancelReveal();
  setStatus(`stopped at ${visibleCount} of ${state.items.length}`);
}

function skipToLatest() {
  const cards = $("feed").querySelectorAll(".card");
  const latestCard = cards[cards.length - 1];
  if (!latestCard) return;
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  latestCard.scrollIntoView({
    behavior: reducedMotion ? "auto" : "smooth",
    block: "end",
  });
}

function moveToTop() {
  const search = $("feed-form");
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  search.scrollIntoView({
    behavior: reducedMotion ? "auto" : "smooth",
    block: "start",
  });
  window.setTimeout(() => $("query").focus({ preventScroll: true }), reducedMotion ? 0 : 350);
}

function clearFeed() {
  const feed = $("feed");
  if (videoPlaybackObserver) feed.querySelectorAll("video").forEach((video) => videoPlaybackObserver.unobserve(video));
  while (feed.firstChild) feed.removeChild(feed.firstChild);
  setSkipLatestVisible(false);
  setMoveTopVisible(false);
  setFeedOverlayFloating(false);
}

function formatDate(value) {
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium" }).format(date);
}

function externalLink(item, text = "open source") {
  const link = document.createElement("a");
  link.href = item.url;
  link.target = "_blank";
  link.rel = "noreferrer noopener";
  link.textContent = text;
  return link;
}

function createMedia(item) {
  const wrapper = document.createElement("div");
  wrapper.className = "card-media is-loading";
  wrapper.textContent = "loading media…";
  let started = false;

  const showSourceLink = () => {
    wrapper.replaceChildren(externalLink(item, "open source link"));
    wrapper.className = "card-media is-ready card-link";
  };

  const showResolvedMedia = (resolved) => {
    if (!resolved || !["video", "image"].includes(resolved.kind) || !resolved.url) {
      showSourceLink();
      return;
    }
    const media = document.createElement(resolved.kind === "video" ? "video" : "img");
    media.referrerPolicy = "no-referrer";
    if (resolved.kind === "video") {
      media.autoplay = true;
      media.controls = false;
      media.defaultMuted = true;
      media.loop = true;
      media.muted = true;
      media.preload = "auto";
      media.playsInline = true;
    } else {
      media.alt = item.label || "Feed item";
      media.loading = "eager";
      media.decoding = "async";
    }
    media.addEventListener("error", showSourceLink, { once: true });
    media.addEventListener(resolved.kind === "video" ? "loadeddata" : "load", () => {
      wrapper.className = "card-media is-ready";
      if (resolved.kind === "video") {
        if (videoPlaybackObserver) videoPlaybackObserver.observe(media);
        else media.play().catch(() => {});
      }
    }, { once: true });
    wrapper.replaceChildren(media);
    media.src = resolved.url;
    if (resolved.kind === "video") media.load();
  };

  const load = async () => {
    if (started) return;
    started = true;
    try {
      const response = await fetch(`/api/feed/${item.content_link_id}/media`);
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(payload.detail || "media unavailable");
      showResolvedMedia(payload);
    } catch (_) {
      showSourceLink();
    }
  };
  return { element: wrapper, load };
}

function appendMeta(meta, text, className = "") {
  if (!text) return;
  const element = document.createElement("span");
  if (className) element.className = className;
  element.textContent = text;
  meta.appendChild(element);
}

function feedbackButton(className, action, text, count, label) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = className;
  button.dataset.action = action;
  button.setAttribute("aria-label", label);
  button.textContent = text;
  if (count !== undefined) {
    const countElement = document.createElement("span");
    countElement.dataset.count = action === "upvote" ? "upvotes" : "downvotes";
    countElement.textContent = ` ${count}`;
    button.appendChild(countElement);
  }
  return button;
}

function renderCard(item) {
  const card = document.createElement("article");
  card.className = "card";
  card.dataset.contentLinkId = String(item.content_link_id);
  card._item = item;

  const media = createMedia(item);
  if (media) card.appendChild(media.element);

  const body = document.createElement("div");
  body.className = "card-body";

  const title = document.createElement("div");
  title.className = "card-title";
  title.textContent = item.label || "untitled link";
  body.appendChild(title);

  const meta = document.createElement("div");
  meta.className = "card-meta";
  appendMeta(meta, item.uploaded_date ? `uploaded ${formatDate(item.uploaded_date)}` : "upload date unknown");
  if (item.recovered_at || item.recovery_generation > 0) {
    appendMeta(
      meta,
      item.recovered_at ? `recovered ${formatDate(item.recovered_at)}` : "recovered",
      "recovered",
    );
  }
  body.appendChild(meta);

  const actions = document.createElement("div");
  actions.className = "card-actions";
  actions.appendChild(feedbackButton("upvote", "upvote", "↑", item.upvotes || 0, "Upvote this link"));
  const score = document.createElement("span");
  score.className = "vote-score";
  score.dataset.count = "vote-score";
  score.textContent = `${(item.vote_score || 0) >= 0 ? "+" : ""}${item.vote_score || 0}`;
  score.title = "upvotes minus downvotes";
  actions.appendChild(score);
  actions.appendChild(feedbackButton("downvote", "downvote", "↓", item.downvotes || 0, "Downvote this link"));
  actions.appendChild(feedbackButton("report", "report", "report", undefined, "Report wrong idol"));
  actions.appendChild(feedbackButton("copy", "copy", "copy link", undefined, "Copy the Imgur link"));
  body.appendChild(actions);

  const message = document.createElement("p");
  message.className = "feedback-message";
  message.setAttribute("aria-live", "polite");
  body.appendChild(message);

  card.appendChild(body);
  card._pendingMediaLoad = media.load;
  return card;
}

function renderFeed() {
  cancelReveal();
  clearFeed();
  if (!state.items.length) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = "no little links found — try another search ♡";
    $("feed").appendChild(empty);
    setStatus("0 links");
    return;
  }

  const token = state.revealToken;
  state.visibleCount = 0;
  const revealNext = () => {
    if (token !== state.revealToken) return;
    const card = renderCard(state.items[state.visibleCount]);
    $("feed").appendChild(card);
    card._pendingMediaLoad();
    state.visibleCount += 1;
    setFeedOverlayFloating(true);
    setSkipLatestVisible(true);
    setMoveTopVisible(true);

    if (state.visibleCount < state.items.length) {
      setStatus(`showing ${state.visibleCount} of ${state.items.length} · next link in 2 seconds`);
      setRevealProgress(true);
      setStopVisible(true);
      state.revealTimer = window.setTimeout(revealNext, REVEAL_DELAY_MS);
    } else {
      state.revealTimer = null;
      setRevealProgress(false);
      setStopVisible(false);
      setStatus(`${state.visibleCount} link${state.visibleCount === 1 ? "" : "s"} shown`);
    }
  };
  revealNext();
}

function setFeedbackMessage(card, text) {
  card.querySelector(".feedback-message").textContent = text;
}

function updateFeedback(card, payload) {
  const upvotes = card.querySelector('[data-count="upvotes"]');
  const downvotes = card.querySelector('[data-count="downvotes"]');
  const score = card.querySelector('[data-count="vote-score"]');
  if (upvotes) upvotes.textContent = ` ${payload.upvotes}`;
  if (downvotes) downvotes.textContent = ` ${payload.downvotes}`;
  if (score) score.textContent = `${payload.vote_score >= 0 ? "+" : ""}${payload.vote_score}`;
}

async function handleFeedback(card, control) {
  const id = card.dataset.contentLinkId;
  const action = control.dataset.action;
  if (action === "copy") {
    control.disabled = true;
    try {
      const copied = await copyLink(card, control);
      if (copied) await recordImplicitUpvote(card, id);
    } finally {
      control.disabled = false;
    }
    return;
  }

  const reportReason = action === "report" ? "wrong_idol" : "";
  const endpoint = action === "report"
    ? `/api/feed/${id}/report?reason=${encodeURIComponent(reportReason)}`
    : `/api/feed/${id}/vote/${action === "upvote" ? "up" : "down"}`;
  control.disabled = true;
  try {
    const response = await fetch(endpoint, { method: "POST" });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.detail || "That action could not be recorded.");
    updateFeedback(card, payload);
    if (reportReason === "wrong_idol") {
      setFeedbackMessage(card, "thanks — wrong idol report recorded ♡");
    } else {
      setFeedbackMessage(card, "vote recorded ♡");
    }
  } catch (error) {
    setFeedbackMessage(card, error.message || "That action could not be recorded.");
  } finally {
    control.disabled = false;
  }
}

async function copyLink(card, button) {
  const message = card.querySelector(".feedback-message");
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(card._item.url);
    } else {
      const textarea = document.createElement("textarea");
      textarea.value = card._item.url;
      textarea.setAttribute("readonly", "");
      textarea.style.position = "fixed";
      textarea.style.opacity = "0";
      document.body.appendChild(textarea);
      textarea.select();
      document.execCommand("copy");
      textarea.remove();
    }
    message.textContent = "link copied ♡";
    return true;
  } catch (_) {
    message.textContent = "couldn't copy automatically — use the source link";
    return false;
  } finally {
    button.blur();
  }
}

async function recordImplicitUpvote(card, id) {
  try {
    const response = await fetch(`/api/feed/${id}/vote/up`, { method: "POST" });
    if (!response.ok) return;
    const payload = await response.json().catch(() => ({}));
    updateFeedback(card, payload);
  } catch (_) {
    // Copying is the primary action; voting failures should remain unobtrusive.
  }
}

async function loadFeed(event) {
  if (event) event.preventDefault();
  $("query").blur();
  cancelReveal();
  setStatus("finding little links…");
  const query = $("query").value.trim();
  const requestedLimit = Number($("limit").value);
  const limit = ALLOWED_LIMITS.has(requestedLimit) ? requestedLimit : 15;
  const requestedSort = $("sort").value;
  const sort = ALLOWED_SORTS.has(requestedSort) ? requestedSort : "random";
  const params = new URLSearchParams({ limit: String(limit), sort });
  const submitButton = $("feed-form").querySelector('button[type="submit"]');
  submitButton.disabled = true;
  if (query) params.set("query", query);
  try {
    const response = await fetch(`/api/feed?${params.toString()}`);
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.detail || "feed unavailable");
    state.items = payload.items || [];
    renderFeed();
  } catch (error) {
    if (!state.items.length) {
      clearFeed();
      const message = document.createElement("p");
      message.className = "empty";
      message.textContent = error.message || "feed unavailable — try again shortly";
      $("feed").appendChild(message);
    }
    setStatus(error.message || "something went wrong");
  } finally {
    submitButton.disabled = false;
  }
}

$("feed-form").addEventListener("submit", loadFeed);
$("stop-feed").addEventListener("click", stopFeed);
$("skip-latest").addEventListener("click", skipToLatest);
$("move-top").addEventListener("click", moveToTop);
$("feed").addEventListener("click", (event) => {
  const button = event.target.closest("button[data-action]");
  if (!button) return;
  const card = button.closest(".card");
  if (card) handleFeedback(card, button);
});
