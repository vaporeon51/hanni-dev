const ALLOWED_LIMITS = new Set([1, 15, 30]);
const ALLOWED_SORTS = new Set(["random", "latest", "oldest", "top"]);
const REVEAL_DELAY_MS = 2000;
const VIEW_CACHE_CAPACITY = 1;
const state = {
  items: [],
  revealTimer: null,
  revealToken: 0,
  visibleCount: 0,
  mode: "feed",
  collectionLabel: "",
  historyKey: "",
  navigationToken: 0,
};
const viewCache = new Map();

const $ = (id) => document.getElementById(id);

function newHistoryKey() {
  if (window.crypto?.randomUUID) return window.crypto.randomUUID();
  return `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

function collectionIdFromLocation() {
  const value = new URL(window.location.href).searchParams.get("collection");
  return /^\d+$/.test(value || "") && Number(value) > 0 ? Number(value) : null;
}

function initializeHistory() {
  const collectionId = collectionIdFromLocation();
  state.mode = collectionId ? "collection" : "feed";
  state.historyKey = window.history.state?.viewKey || newHistoryKey();
  window.history.replaceState(
    { viewKey: state.historyKey, mode: state.mode, collectionId },
    "",
    window.location.href,
  );
  if ("scrollRestoration" in window.history) window.history.scrollRestoration = "manual";
  return collectionId;
}

function lockMobileMediaHeight() {
  if (!window.matchMedia("(max-width: 620px)").matches) {
    document.documentElement.style.removeProperty("--mobile-media-max-height");
    return;
  }
  const viewportHeight = window.visualViewport?.height || window.innerHeight;
  document.documentElement.style.setProperty(
    "--mobile-media-max-height",
    `${Math.floor(viewportHeight * 0.72)}px`,
  );
}

lockMobileMediaHeight();
window.addEventListener("orientationchange", () => {
  window.setTimeout(lockMobileMediaHeight, 250);
});

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

function setJumpBottomVisible(visible) {
  $("jump-bottom").hidden = !visible;
}

function setJumpTopVisible(visible) {
  $("jump-top").hidden = !visible;
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

function jumpToBottom() {
  const cards = $("feed").querySelectorAll(".card");
  const latestCard = cards[cards.length - 1];
  if (!latestCard) return;
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  latestCard.scrollIntoView({
    behavior: reducedMotion ? "auto" : "smooth",
    block: "end",
  });
}

function jumpToTop() {
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
  setJumpBottomVisible(false);
  setJumpTopVisible(false);
  setFeedOverlayFloating(false);
}

function setCollectionHeading(label = "", count = 0) {
  const heading = $("collection-heading");
  heading.replaceChildren();
  heading.hidden = !label;
  if (!label) return;
  const title = document.createElement("strong");
  title.textContent = label;
  heading.append(title, ` · set of ${count}`);
}

function disposeView(snapshot) {
  snapshot.nodes.forEach((node) => {
    node.querySelectorAll?.("video").forEach((video) => {
      if (videoPlaybackObserver) videoPlaybackObserver.unobserve(video);
      video.pause();
      video.removeAttribute("src");
      video.load();
    });
  });
}

function cacheView(key, snapshot) {
  const previous = viewCache.get(key);
  if (previous) disposeView(previous);
  viewCache.set(key, snapshot);
  while (viewCache.size > VIEW_CACHE_CAPACITY) {
    const oldestKey = viewCache.keys().next().value;
    const oldest = viewCache.get(oldestKey);
    viewCache.delete(oldestKey);
    if (oldest) disposeView(oldest);
  }
}

function captureCurrentView() {
  const wasRevealing = state.revealTimer !== null;
  if (state.revealTimer !== null) window.clearTimeout(state.revealTimer);
  state.revealTimer = null;
  state.revealToken += 1;
  const nodes = Array.from($("feed").childNodes);
  nodes.forEach((node) => node.remove());
  return {
    nodes,
    items: state.items,
    visibleCount: state.visibleCount,
    mode: state.mode,
    collectionLabel: state.collectionLabel,
    wasRevealing,
    statusText: $("status").textContent,
    query: $("query").value,
    sort: $("sort").value,
    limit: $("limit").value,
    scrollY: window.scrollY,
  };
}

function storeCurrentView() {
  cacheView(state.historyKey, captureCurrentView());
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
      showCollectionLink(item, Number(payload.collection_count) || 0);
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

function showCollectionLink(item, count) {
  if (state.mode !== "feed" || count < 2) return;
  const card = $("feed").querySelector(`[data-content-link-id="${item.content_link_id}"]`);
  const header = card?.querySelector(".card-header");
  if (!header || header.querySelector(".collection-link")) return;
  const link = document.createElement("a");
  link.className = "collection-link";
  link.href = `/?collection=${item.content_link_id}`;
  link.dataset.collectionId = String(item.content_link_id);
  link.textContent = `view set (${count}) →`;
  header.appendChild(link);
}

function feedbackButton(className, action, text, label) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = className;
  button.dataset.action = action;
  button.setAttribute("aria-label", label);
  button.textContent = text;
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

  const header = document.createElement("div");
  header.className = "card-header";

  const title = document.createElement("div");
  title.className = "card-title";
  title.textContent = item.label || "untitled link";
  header.appendChild(title);
  body.appendChild(header);

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
  actions.appendChild(feedbackButton("upvote", "upvote", "↑", "Upvote this link"));
  const score = document.createElement("span");
  score.className = "vote-score";
  score.dataset.count = "vote-score";
  score.textContent = `${(item.vote_score || 0) >= 0 ? "+" : ""}${item.vote_score || 0}`;
  score.title = "upvotes minus downvotes";
  actions.appendChild(score);
  actions.appendChild(feedbackButton("downvote", "downvote", "↓", "Downvote this link"));
  actions.appendChild(feedbackButton("report", "report", "report", "Report wrong idol"));
  actions.appendChild(feedbackButton("copy", "copy", "copy link", "Copy the Imgur link"));
  body.appendChild(actions);

  const message = document.createElement("p");
  message.className = "feedback-message";
  message.setAttribute("aria-live", "polite");
  body.appendChild(message);

  card.appendChild(body);
  card._pendingMediaLoad = media.load;
  return card;
}

function revealNext(token) {
  if (token !== state.revealToken || state.visibleCount >= state.items.length) return;
  const card = renderCard(state.items[state.visibleCount]);
  $("feed").appendChild(card);
  card._pendingMediaLoad();
  state.visibleCount += 1;
  setFeedOverlayFloating(true);
  setJumpBottomVisible(true);
  setJumpTopVisible(true);

  if (state.visibleCount < state.items.length) {
    setStatus(`showing ${state.visibleCount} of ${state.items.length} · next link in 2 seconds`);
    setRevealProgress(true);
    setStopVisible(true);
    state.revealTimer = window.setTimeout(() => revealNext(token), REVEAL_DELAY_MS);
  } else {
    state.revealTimer = null;
    setRevealProgress(false);
    setStopVisible(false);
    setStatus(`${state.visibleCount} link${state.visibleCount === 1 ? "" : "s"} shown`);
  }
}

function resumeReveal() {
  if (state.visibleCount >= state.items.length) return;
  const token = state.revealToken;
  setStatus(`showing ${state.visibleCount} of ${state.items.length} · next link in 2 seconds`);
  setRevealProgress(true);
  setStopVisible(true);
  state.revealTimer = window.setTimeout(() => revealNext(token), REVEAL_DELAY_MS);
}

function renderFeed() {
  cancelReveal();
  clearFeed();
  if (!state.items.length) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = state.mode === "collection"
      ? "this set has no available links ♡"
      : "no little links found — try another search ♡";
    $("feed").appendChild(empty);
    setStatus("0 links");
    return;
  }

  state.visibleCount = 0;
  revealNext(state.revealToken);
}

function restoreView(snapshot) {
  cancelReveal();
  clearFeed();
  state.items = snapshot.items;
  state.visibleCount = snapshot.visibleCount;
  state.mode = snapshot.mode;
  state.collectionLabel = snapshot.collectionLabel;
  $("query").value = snapshot.query;
  $("sort").value = snapshot.sort;
  $("limit").value = snapshot.limit;
  $("feed").append(...snapshot.nodes);
  setCollectionHeading(
    state.mode === "collection" ? state.collectionLabel : "",
    state.mode === "collection" ? state.items.length : 0,
  );
  const hasCards = state.visibleCount > 0;
  setFeedOverlayFloating(hasCards);
  setJumpBottomVisible(hasCards);
  setJumpTopVisible(hasCards);
  if (snapshot.wasRevealing && state.visibleCount < state.items.length) {
    resumeReveal();
  } else {
    setRevealProgress(false);
    setStopVisible(false);
    setStatus(snapshot.statusText);
  }
  window.requestAnimationFrame(() => window.scrollTo({ top: snapshot.scrollY, behavior: "auto" }));
}

function setFeedbackMessage(card, text) {
  card.querySelector(".feedback-message").textContent = text;
}

function updateFeedback(card, payload) {
  const score = card.querySelector('[data-count="vote-score"]');
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

async function loadCollection(contentLinkId) {
  const navigationToken = ++state.navigationToken;
  cancelReveal();
  clearFeed();
  state.mode = "collection";
  state.collectionLabel = "";
  state.items = [];
  state.visibleCount = 0;
  setCollectionHeading();
  setStatus("finding this set…");
  try {
    const response = await fetch(`/api/collections/${contentLinkId}`);
    const payload = await response.json().catch(() => ({}));
    if (navigationToken !== state.navigationToken) return;
    if (!response.ok) throw new Error(payload.detail || "set unavailable");
    state.items = payload.items || [];
    state.collectionLabel = payload.label || "content set";
    setCollectionHeading(state.collectionLabel, state.items.length);
    window.scrollTo({ top: 0, behavior: "auto" });
    renderFeed();
  } catch (error) {
    if (navigationToken !== state.navigationToken) return;
    const message = document.createElement("p");
    message.className = "empty";
    message.textContent = error.message || "set unavailable — try again shortly";
    $("feed").appendChild(message);
    setStatus(error.message || "set unavailable");
  }
}

function navigateToCollection(contentLinkId, href) {
  storeCurrentView();
  state.historyKey = newHistoryKey();
  window.history.pushState(
    { viewKey: state.historyKey, mode: "collection", collectionId: contentLinkId },
    "",
    href,
  );
  loadCollection(contentLinkId);
}

function beginFeedNavigation() {
  if (state.mode !== "collection") return;
  storeCurrentView();
  state.historyKey = newHistoryKey();
  window.history.pushState(
    { viewKey: state.historyKey, mode: "feed", collectionId: null },
    "",
    "/",
  );
  state.mode = "feed";
  state.collectionLabel = "";
  state.items = [];
  state.visibleCount = 0;
  setCollectionHeading();
}

async function loadFeed(event) {
  if (event) event.preventDefault();
  beginFeedNavigation();
  const navigationToken = ++state.navigationToken;
  state.mode = "feed";
  state.collectionLabel = "";
  setCollectionHeading();
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
    if (navigationToken !== state.navigationToken) return;
    if (!response.ok) throw new Error(payload.detail || "feed unavailable");
    state.items = payload.items || [];
    renderFeed();
  } catch (error) {
    if (navigationToken !== state.navigationToken) return;
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

window.addEventListener("popstate", (event) => {
  state.navigationToken += 1;
  const nextHistoryKey = event.state?.viewKey || newHistoryKey();
  const snapshot = viewCache.get(nextHistoryKey);
  if (snapshot) viewCache.delete(nextHistoryKey);
  storeCurrentView();
  state.historyKey = nextHistoryKey;
  if (snapshot) {
    restoreView(snapshot);
    return;
  }
  const collectionId = collectionIdFromLocation();
  if (collectionId) {
    loadCollection(collectionId);
    return;
  }
  cancelReveal();
  clearFeed();
  state.mode = "feed";
  state.collectionLabel = "";
  state.items = [];
  state.visibleCount = 0;
  setCollectionHeading();
  setStatus("");
  window.scrollTo({ top: 0, behavior: "auto" });
});

$("feed-form").addEventListener("submit", loadFeed);
$("stop-feed").addEventListener("click", stopFeed);
$("jump-bottom").addEventListener("click", jumpToBottom);
$("jump-top").addEventListener("click", jumpToTop);
$("feed").addEventListener("click", (event) => {
  const collectionLink = event.target.closest("a.collection-link");
  if (collectionLink) {
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    navigateToCollection(Number(collectionLink.dataset.collectionId), collectionLink.href);
    return;
  }
  const button = event.target.closest("button[data-action]");
  if (!button) return;
  const card = button.closest(".card");
  if (card) handleFeedback(card, button);
});

const initialCollectionId = initializeHistory();
if (initialCollectionId) loadCollection(initialCollectionId);
