const BATCH_SIZE = 5;
const MEDIA_PRELOAD_MARGIN = "900px 0px";
const FIRST_MEDIA_HEAD_START_MS = 360;
const MEDIA_STAGGER_MS = 110;
const MEDIA_RETRY_DELAYS_MS = [1500, 4000, 9000];
const CONTINUATION_GAP_MS = 1100;
const VIEW_CACHE_CAPACITY = 1;
const state = {
  items: [],
  mode: "feed",
  collectionLabel: "",
  historyKey: "",
  navigationToken: 0,
  hasMore: false,
  loadingMore: false,
  retryContinuation: false,
  nextContinuationAt: 0,
  continuationTimer: null,
  query: "",
  sort: "random",
};
const viewCache = new Map();
const pendingMediaStarts = new Set();

const $ = (id) => document.getElementById(id);
const dateFormatter = new Intl.DateTimeFormat(undefined, { dateStyle: "medium" });
const reducedMotionQuery = window.matchMedia("(prefers-reduced-motion: reduce)");

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
        if (entry.isIntersecting && entry.intersectionRatio > 0) {
          entry.target.play().catch(() => {});
        } else {
          entry.target.pause();
        }
      });
    }, { threshold: [0, 0.01] })
  : null;

const mediaWindowObserver = "IntersectionObserver" in window
  ? new IntersectionObserver((entries) => {
      entries.forEach((entry) => entry.target._mediaController?.setNearViewport(entry.isIntersecting));
    }, { rootMargin: MEDIA_PRELOAD_MARGIN, threshold: 0 })
  : null;

const feedEndObserver = "IntersectionObserver" in window
  ? new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting) && state.mode === "feed" && !state.loadingMore) {
        loadMoreFeed();
      }
    }, { rootMargin: "900px 0px", threshold: 0 })
  : null;

function refreshFeedSentinelObserver() {
  if (!feedEndObserver) return;
  const sentinel = $("feed-sentinel");
  feedEndObserver.unobserve(sentinel);
  feedEndObserver.observe(sentinel);
}

function setStatus(text) {
  const status = $("status");
  status.textContent = text;
  status.hidden = !text;
}

function setSentinel(text = "", stateClass = "") {
  const sentinel = $("feed-sentinel");
  sentinel.className = `feed-sentinel${stateClass ? ` ${stateClass}` : ""}`;
  sentinel.textContent = text;
}

function cancelContinuationTimer() {
  if (state.continuationTimer === null) return;
  window.clearTimeout(state.continuationTimer);
  state.continuationTimer = null;
}

function sentinelIsNearViewport() {
  const sentinel = $("feed-sentinel");
  return sentinel.getBoundingClientRect().top <= window.innerHeight + 900;
}

function scheduleFeedContinuation() {
  if (
    state.continuationTimer !== null
    || state.mode !== "feed"
    || !state.hasMore
    || state.loadingMore
  ) return;
  const delay = Math.max(0, state.nextContinuationAt - Date.now());
  state.continuationTimer = window.setTimeout(() => {
    state.continuationTimer = null;
    if (state.mode === "feed" && state.hasMore && sentinelIsNearViewport()) loadMoreFeed();
  }, delay);
}

function setTimelineToolsVisible(visible) {
  $("timeline-tools").hidden = !visible;
}

let timelineToolsFrame = null;
function updateTimelineTools() {
  setTimelineToolsVisible(state.mode === "feed" && window.scrollY > 500);
}

function scheduleTimelineToolsUpdate() {
  if (timelineToolsFrame !== null) return;
  timelineToolsFrame = window.requestAnimationFrame(() => {
    timelineToolsFrame = null;
    updateTimelineTools();
  });
}

function focusSearch() {
  const search = $("feed-form");
  const reducedMotion = reducedMotionQuery.matches;
  search.scrollIntoView({ behavior: reducedMotion ? "auto" : "smooth", block: "start" });
  window.setTimeout(() => $("query").focus({ preventScroll: true }), reducedMotion ? 0 : 350);
}

function jumpToTop() {
  const reducedMotion = reducedMotionQuery.matches;
  $("feed-form").scrollIntoView({
    behavior: reducedMotion ? "auto" : "smooth",
    block: "start",
  });
}

function clearFeed() {
  cancelContinuationTimer();
  cancelPendingMediaStarts();
  const feed = $("feed");
  feed.querySelectorAll(".card").forEach((card) => card._mediaController?.dispose());
  while (feed.firstChild) feed.removeChild(feed.firstChild);
  setSentinel();
  setStatus("");
  setTimelineToolsVisible(false);
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
    node._mediaController?.dispose();
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
  cancelPendingMediaStarts();
  // Read this before detaching the cards. Removing the feed collapses the
  // document and can clamp window.scrollY back to zero on mobile browsers.
  const scrollY = window.scrollY;
  const nodes = Array.from($("feed").childNodes);
  nodes.forEach((node) => node.remove());
  return {
    nodes,
    items: state.items,
    mode: state.mode,
    collectionLabel: state.collectionLabel,
    statusText: $("status").textContent,
    query: $("query").value,
    sort: $("sort").value,
    hasMore: state.hasMore,
    scrollY,
  };
}

function storeCurrentView() {
  cacheView(state.historyKey, captureCurrentView());
}

function formatDate(value) {
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  return dateFormatter.format(date);
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
  let media = null;
  let resolved = null;
  let loading = false;
  let failed = false;
  let nearViewport = false;
  let disposed = false;
  let requestController = null;
  let retryTimer = null;
  let retryCount = 0;

  const cancelRetry = () => {
    if (retryTimer === null) return;
    window.clearTimeout(retryTimer);
    retryTimer = null;
  };

  const releaseMedia = ({ preserveHeight = false } = {}) => {
    if (preserveHeight) {
      const height = wrapper.getBoundingClientRect().height;
      if (height > 0) wrapper.style.minHeight = `${Math.ceil(height)}px`;
    }
    if (media?.tagName === "VIDEO") {
      videoPlaybackObserver?.unobserve(media);
      media.pause();
    }
    if (media) {
      media.onerror = null;
      media.onload = null;
      media.onloadeddata = null;
      media.removeAttribute("src");
      if (media.tagName === "VIDEO") media.load();
    }
    media = null;
  };

  const showSourceLink = () => {
    if (disposed) return;
    failed = true;
    cancelRetry();
    releaseMedia();
    wrapper.replaceChildren(externalLink(item, "open source link"));
    wrapper.className = "card-media is-ready card-link";
    wrapper.style.minHeight = "";
  };

  const scheduleRetry = (delay) => {
    if (disposed || !nearViewport) return;
    cancelRetry();
    releaseMedia({ preserveHeight: true });
    wrapper.replaceChildren();
    wrapper.className = "card-media is-loading";
    wrapper.textContent = "media is catching up…";
    retryTimer = window.setTimeout(() => {
      retryTimer = null;
      if (nearViewport && !disposed) load();
    }, delay);
  };

  const handleMediaError = () => {
    if (retryCount >= MEDIA_RETRY_DELAYS_MS.length) {
      showSourceLink();
      return;
    }
    scheduleRetry(MEDIA_RETRY_DELAYS_MS[retryCount]);
    retryCount += 1;
  };

  const showResolvedMedia = (payload) => {
    if (!payload || !["video", "image"].includes(payload.kind) || !payload.url) {
      showSourceLink();
      return;
    }
    resolved = payload;
    if (disposed || !nearViewport) return;
    showCollectionLink(item, Number(payload.collection_count) || 0);
    if (!media || (media.tagName === "VIDEO") !== (resolved.kind === "video")) {
      media = document.createElement(resolved.kind === "video" ? "video" : "img");
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
    }
    wrapper.replaceChildren(media);
    wrapper.className = "card-media is-loading";
    media.onerror = handleMediaError;
    const onReady = () => {
      if (disposed || !media) return;
      wrapper.className = "card-media is-ready";
      wrapper.style.minHeight = "";
      media.style.width = "";
      media.style.height = "";
      if (resolved.kind === "video") {
        if (videoPlaybackObserver) videoPlaybackObserver.observe(media);
        else media.play().catch(() => {});
      }
    };
    if (resolved.kind === "video") media.onloadeddata = onReady;
    else media.onload = onReady;
    media.src = resolved.url;
    if (resolved.kind === "video") media.load();
  };

  const load = async () => {
    if (failed || loading || retryTimer !== null) return;
    if (resolved) {
      if (!media?.getAttribute("src")) showResolvedMedia(resolved);
      return;
    }
    loading = true;
    const currentController = new AbortController();
    requestController = currentController;
    try {
      const response = await fetch(`/api/feed/${item.content_link_id}/media`, {
        signal: currentController.signal,
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        const error = new Error(payload.detail || "media unavailable");
        error.isTransient = [429, 502, 503, 504].includes(response.status);
        error.retryAfter = Math.max(0, Number(response.headers.get("Retry-After")) || 0) * 1000;
        throw error;
      }
      showResolvedMedia(payload);
    } catch (error) {
      if (error.name === "AbortError") return;
      if (error.isTransient && retryCount < MEDIA_RETRY_DELAYS_MS.length && nearViewport) {
        const delay = Math.max(error.retryAfter, MEDIA_RETRY_DELAYS_MS[retryCount]);
        retryCount += 1;
        scheduleRetry(delay);
      } else {
        showSourceLink();
      }
    } finally {
      if (requestController === currentController) requestController = null;
      loading = false;
    }
  };

  const unload = () => {
    cancelRetry();
    if (!media || media.tagName !== "VIDEO" || !resolved || !media.getAttribute("src")) return;
    const rect = media.getBoundingClientRect();
    if (rect.height > 0) {
      wrapper.style.minHeight = `${Math.ceil(wrapper.getBoundingClientRect().height)}px`;
      media.style.width = `${Math.ceil(rect.width)}px`;
      media.style.height = `${Math.ceil(rect.height)}px`;
    }
    videoPlaybackObserver?.unobserve(media);
    media.pause();
    media.removeAttribute("src");
    media.load();
    wrapper.className = "card-media is-loading";
    wrapper.replaceChildren(media);
  };

  const controller = {
    element: wrapper,
    observe() {
      wrapper._mediaController = controller;
      if (mediaWindowObserver) mediaWindowObserver.observe(wrapper);
      else {
        nearViewport = true;
        load();
      }
    },
    setNearViewport(isNear) {
      nearViewport = isNear;
      if (isNear) load();
      else unload();
    },
    dispose() {
      disposed = true;
      nearViewport = false;
      cancelRetry();
      requestController?.abort();
      requestController = null;
      mediaWindowObserver?.unobserve(wrapper);
      releaseMedia();
    },
  };
  wrapper._mediaController = controller;
  return controller;
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
  card._mediaController = media;
  return card;
}

function cancelPendingMediaStarts() {
  pendingMediaStarts.forEach((timer) => window.clearTimeout(timer));
  pendingMediaStarts.clear();
}

function scheduleMediaStart(controller, delay) {
  const timer = window.setTimeout(() => {
    pendingMediaStarts.delete(timer);
    if (controller.element.isConnected) controller.observe();
  }, delay);
  pendingMediaStarts.add(timer);
}

function appendCards(items, { staggerMedia = true } = {}) {
  const cards = items.map((item) => renderCard(item));
  $("feed").append(...cards);
  cards.forEach((card, index) => {
    if (!staggerMedia || index === 0) {
      card._mediaController.observe();
      return;
    }
    scheduleMediaStart(
      card._mediaController,
      FIRST_MEDIA_HEAD_START_MS + (index - 1) * MEDIA_STAGGER_MS,
    );
  });
}

function renderFeed() {
  clearFeed();
  if (!state.items.length) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = state.mode === "collection"
      ? "this set has no available links ♡"
      : "no little links found — try another search ♡";
    $("feed").appendChild(empty);
    setStatus("0 links");
    setSentinel(state.mode === "feed" ? (state.hasMore ? "" : "end of results") : "");
    return;
  }
  appendCards(state.items);
  setStatus(`${state.items.length} link${state.items.length === 1 ? "" : "s"} loaded`);
  setSentinel(state.mode === "feed" ? (state.hasMore ? "" : "end of results") : "");
  updateTimelineTools();
}

function restoreView(snapshot) {
  clearFeed();
  state.items = snapshot.items;
  state.mode = snapshot.mode;
  state.collectionLabel = snapshot.collectionLabel;
  state.query = snapshot.query;
  state.sort = snapshot.sort === "top" ? "top" : "random";
  state.hasMore = snapshot.hasMore;
  state.loadingMore = false;
  state.retryContinuation = false;
  $("query").value = snapshot.query;
  $("sort").value = state.sort;
  $("feed").append(...snapshot.nodes);
  $("feed").querySelectorAll(".card").forEach((card) => card._mediaController?.observe());
  setCollectionHeading(
    state.mode === "collection" ? state.collectionLabel : "",
    state.mode === "collection" ? state.items.length : 0,
  );
  setSentinel(state.mode === "feed" ? (state.hasMore ? "" : "end of results") : "");
  setStatus(snapshot.statusText);
  updateTimelineTools();
  refreshFeedSentinelObserver();
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
  clearFeed();
  state.mode = "collection";
  state.collectionLabel = "";
  state.items = [];
  state.hasMore = false;
  state.loadingMore = false;
  state.retryContinuation = false;
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
  state.hasMore = true;
  state.loadingMore = false;
  state.retryContinuation = false;
  setCollectionHeading();
}

async function loadFeed(event) {
  if (event) event.preventDefault();
  beginFeedNavigation();
  const navigationToken = ++state.navigationToken;
  state.mode = "feed";
  state.collectionLabel = "";
  state.query = $("query").value.trim();
  state.sort = $("sort").value === "top" ? "top" : "random";
  state.items = [];
  state.hasMore = true;
  state.loadingMore = true;
  state.retryContinuation = false;
  state.nextContinuationAt = 0;
  setCollectionHeading();
  $("query").blur();
  clearFeed();
  setStatus("finding little links…");
  setSentinel("finding little links…", "is-loading");
  const params = new URLSearchParams({ limit: String(BATCH_SIZE), sort: state.sort });
  const submitButton = $("feed-form").querySelector('button[type="submit"]');
  submitButton.disabled = true;
  if (state.query) params.set("query", state.query);
  try {
    const response = await fetch(`/api/feed?${params.toString()}`);
    const payload = await response.json().catch(() => ({}));
    if (navigationToken !== state.navigationToken) return;
    if (!response.ok) throw new Error(payload.detail || "feed unavailable");
    const items = payload.items || [];
    state.items = items;
    state.hasMore = payload.has_more !== false && items.length > 0;
    state.retryContinuation = false;
    if (items.length) {
      appendCards(items);
      setStatus(`${items.length} link${items.length === 1 ? "" : "s"} loaded`);
    } else {
      const empty = document.createElement("p");
      empty.className = "empty";
      empty.textContent = "no little links found — try another search ♡";
      $("feed").appendChild(empty);
      setStatus("0 links");
    }
    setSentinel(state.hasMore ? "" : "end of results");
    refreshFeedSentinelObserver();
  } catch (error) {
    if (navigationToken !== state.navigationToken) return;
    state.hasMore = false;
    state.retryContinuation = false;
    setSentinel("couldn't load feed · tap to retry", "is-error");
    setStatus(error.message || "something went wrong");
  } finally {
    if (navigationToken === state.navigationToken) {
      state.loadingMore = false;
      submitButton.disabled = false;
    }
  }
}

async function loadMoreFeed() {
  if (state.mode !== "feed" || !state.hasMore || state.loadingMore) return;
  if (Date.now() < state.nextContinuationAt) {
    scheduleFeedContinuation();
    return;
  }
  const navigationToken = state.navigationToken;
  state.loadingMore = true;
  state.retryContinuation = false;
  setSentinel("finding more little links…", "is-loading");
  try {
    const params = new URLSearchParams({
      limit: String(BATCH_SIZE),
      sort: state.sort,
      continuation: "true",
    });
    if (state.query) params.set("query", state.query);
    if (state.sort !== "random") params.set("offset", String(state.items.length));
    const response = await fetch(`/api/feed?${params.toString()}`);
    const payload = await response.json().catch(() => ({}));
    if (navigationToken !== state.navigationToken) return;
    if (!response.ok) throw new Error(payload.detail || "more links unavailable");
    const knownIds = new Set(state.items.map((item) => item.content_link_id));
    const incoming = (payload.items || []).filter((item) => !knownIds.has(item.content_link_id));
    state.hasMore = payload.has_more !== false && incoming.length > 0;
    state.retryContinuation = false;
    if (incoming.length) {
      state.items.push(...incoming);
      appendCards(incoming);
      setStatus(`${state.items.length} links loaded`);
    }
    state.nextContinuationAt = state.hasMore ? Date.now() + CONTINUATION_GAP_MS : 0;
    setSentinel(state.hasMore ? "" : "end of results");
    refreshFeedSentinelObserver();
  } catch (error) {
    if (navigationToken !== state.navigationToken) return;
    state.hasMore = true;
    state.retryContinuation = true;
    state.nextContinuationAt = 0;
    setSentinel("couldn't load more · tap to retry", "is-error");
    setStatus(error.message || "more links unavailable");
  } finally {
    if (navigationToken === state.navigationToken) state.loadingMore = false;
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
  clearFeed();
  state.mode = "feed";
  state.collectionLabel = "";
  state.items = [];
  state.query = $("query").value.trim();
  state.hasMore = true;
  state.loadingMore = false;
  state.retryContinuation = false;
  setCollectionHeading();
  window.scrollTo({ top: 0, behavior: "auto" });
  loadFeed();
});

$("feed-form").addEventListener("submit", loadFeed);
$("timeline-search").addEventListener("click", focusSearch);
$("timeline-top").addEventListener("click", jumpToTop);
$("feed-sentinel").addEventListener("click", () => {
  if ($("feed-sentinel").classList.contains("is-error") && !state.retryContinuation) loadFeed();
  else loadMoreFeed();
});
window.addEventListener("scroll", scheduleTimelineToolsUpdate, { passive: true });
refreshFeedSentinelObserver();
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
else clearFeed();
