const BATCH_SIZE = 5;
const MEDIA_PRELOAD_MARGIN = "550px 0px";
const FIRST_MEDIA_HEAD_START_MS = 360;
const MEDIA_STAGGER_MS = 110;
const MEDIA_RETRY_DELAYS_MS = [1500, 4000, 9000];
const state = {
  sets: [],
  historyKey: "",
  navigationToken: 0,
  nextCursor: null,
  requestParams: null,
  loadingMore: false,
  retryContinuation: false,
};
const pendingMediaStarts = new Set();

const $ = (id) => document.getElementById(id);
const dateFormatter = new Intl.DateTimeFormat(undefined, { dateStyle: "medium" });
const reducedMotionQuery = window.matchMedia("(prefers-reduced-motion: reduce)");

const viewCache = new Map();

const navigation = window.SiteNavigation;
const filterOptions = { sorts: ["latest", "oldest"], defaultSort: "latest" };
navigation.normalize(filterOptions);

function filtersFromLocation() {
  return navigation.readFilters(filterOptions);
}

function filterUrl(filters) {
  return navigation.filterUrl(filters, { remove: ["collection"] });
}

function applyFilters(filters) {
  $("query").value = filters.query;
  $("sort").value = filters.sort;
}

function storeCurrentView() {
  if (state.loadingMore && !state.sets.length) return;
  cancelPendingMediaStarts();
  const snapshot = {
    nodes: Array.from($("feed").childNodes),
    sets: state.sets,
    nextCursor: state.nextCursor,
    requestParams: state.requestParams,
    statusText: $("status").textContent,
    scrollY: window.scrollY,
  };
  snapshot.nodes.forEach((node) => node.remove());
  // Keep the preceding view, including its carousel positions and scroll offset.
  viewCache.forEach((view) => view.nodes.forEach((node) => {
    node._setMedia?.forEach((media) => media.dispose());
  }));
  viewCache.clear();
  viewCache.set(state.historyKey, snapshot);
}

function restoreView(snapshot) {
  clearFeed();
  $("sets-form").querySelector('button[type="submit"]').disabled = false;
  state.sets = snapshot.sets;
  state.nextCursor = snapshot.nextCursor;
  state.requestParams = snapshot.requestParams;
  state.loadingMore = false;
  state.retryContinuation = false;
  applyFilters(filtersFromLocation());
  $("feed").append(...snapshot.nodes);
  $("feed").querySelectorAll(".set-card").forEach(observeSetCard);
  setStatus(snapshot.statusText);
  setSentinel(state.nextCursor ? "" : "end of results");
  updateTimelineTools();
  refreshSetSentinelObserver();
  window.requestAnimationFrame(() => window.scrollTo({ top: snapshot.scrollY, behavior: "auto" }));
}

function navigateSets(filters) {
  const url = filterUrl(filters);
  if (url.href !== window.location.href) {
    storeCurrentView();
    state.historyKey = navigation.newKey();
    window.history.pushState({ viewKey: state.historyKey }, "", url.href);
  }
  applyFilters(filters);
  loadSets();
  window.scrollTo({ top: 0, behavior: "auto" });
}

function submitSetFilters(event) {
  event.preventDefault();
  const query = $("query").value.trim();
  navigateSets({
    query,
    sort: $("sort").value,
  });
}

function loadViewFromLocation() {
  const filters = filtersFromLocation();
  applyFilters(filters);
  loadSets();
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
window.addEventListener("orientationchange", () => window.setTimeout(lockMobileMediaHeight, 250));

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

const setEndObserver = "IntersectionObserver" in window
  ? new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting) && !state.loadingMore) loadMoreSets();
    }, { rootMargin: "900px 0px", threshold: 0 })
  : null;

function refreshSetSentinelObserver() {
  if (!setEndObserver) return;
  const sentinel = $("feed-sentinel");
  setEndObserver.unobserve(sentinel);
  setEndObserver.observe(sentinel);
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

function setTimelineToolsVisible(visible) {
  $("timeline-tools").hidden = !visible;
}

let timelineToolsFrame = null;
function updateTimelineTools() {
  setTimelineToolsVisible(window.scrollY > 500);
}

function scheduleTimelineToolsUpdate() {
  if (timelineToolsFrame !== null) return;
  timelineToolsFrame = window.requestAnimationFrame(() => {
    timelineToolsFrame = null;
    updateTimelineTools();
  });
}

function focusSearch() {
  const reducedMotion = reducedMotionQuery.matches;
  $("sets-form").scrollIntoView({ behavior: reducedMotion ? "auto" : "smooth", block: "start" });
  window.setTimeout(() => $("query").focus({ preventScroll: true }), reducedMotion ? 0 : 350);
}

function jumpToTop() {
  const reducedMotion = reducedMotionQuery.matches;
  $("sets-form").scrollIntoView({ behavior: reducedMotion ? "auto" : "smooth", block: "start" });
}

function clearFeed() {
  cancelPendingMediaStarts();
  $("feed").querySelectorAll(".set-card").forEach((card) => {
    card._setMedia?.forEach((media) => media.dispose());
  });
  $("feed").replaceChildren();
  setSentinel();
  setStatus("");
  setTimelineToolsVisible(false);
}

function formatDate(value) {
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  return dateFormatter.format(date);
}

function externalLink(item) {
  const link = document.createElement("a");
  link.href = item.url;
  link.target = "_blank";
  link.rel = "noreferrer noopener";
  link.textContent = "open source link";
  return link;
}

function createMedia(item, onUnavailable = () => {}) {
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
    wrapper.replaceChildren(externalLink(item));
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

  const hideUnavailable = () => {
    if (disposed) return;
    controller.dispose();
    onUnavailable();
  };

  const handleMediaError = async () => {
    if (disposed) return;
    // Media elements do not expose HTTP status. Confirm a 404 with a tiny
    // range request before hiding; network/codec errors keep normal retries.
    const probe = new AbortController();
    requestController?.abort();
    requestController = probe;
    const timeout = window.setTimeout(() => probe.abort(), 15000);
    try {
      const response = await fetch(resolved.url, {
        headers: { Range: "bytes=0-0" }, signal: probe.signal, cache: "no-store",
      });
      response.body?.cancel().catch(() => {});
      if (response.status === 404) {
        hideUnavailable();
        return;
      }
    } catch (_) {
      // An inconclusive probe must not hide content.
    } finally {
      window.clearTimeout(timeout);
      if (requestController === probe) requestController = null;
    }
    if (disposed) return;
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
    if (!media || (media.tagName === "VIDEO") !== (resolved.kind === "video")) {
      media = window.HanniMedia.create(resolved.kind, item.label || "Set item");
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
    if (disposed || failed || loading || retryTimer !== null) return;
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
      if (response.status === 404) {
        hideUnavailable();
        return;
      }
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
      if (disposed) return;
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

function renderMeta(meta, item) {
  meta.replaceChildren();
  appendMeta(meta, item.uploaded_date ? `uploaded ${formatDate(item.uploaded_date)}` : "upload date unknown");
  if (item.recovered_at || item.recovery_generation > 0) {
    appendMeta(
      meta,
      item.recovered_at ? `recovered ${formatDate(item.recovered_at)}` : "recovered",
      "recovered",
    );
  }
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

function createSetBody(contentSet, item) {
  const body = document.createElement("div");
  body.className = "card-body";

  const header = document.createElement("div");
  header.className = "card-header";
  const title = document.createElement("a");
  title.className = "card-title filter-link";
  title.dataset.filterQuery = contentSet.label || item.label || "content set";
  title.href = filterUrl({
    query: title.dataset.filterQuery,
    sort: $("sort").value,
  }).href;
  title.textContent = title.dataset.filterQuery;
  const position = document.createElement("span");
  position.className = "set-position";
  position.dataset.setPosition = "";
  position.textContent = `1 / ${contentSet.items.length}`;
  header.append(title, position);
  body.appendChild(header);

  const meta = document.createElement("div");
  meta.className = "card-meta";
  renderMeta(meta, item);
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
  actions.appendChild(feedbackButton("copy", "copy", "copy link", "Copy this link"));
  body.appendChild(actions);

  const message = document.createElement("p");
  message.className = "feedback-message";
  message.setAttribute("aria-live", "polite");
  body.appendChild(message);
  return body;
}

function setFeedbackMessage(card, text) {
  card.querySelector(".feedback-message").textContent = text;
}

function activateSlide(card, index) {
  const items = card._setItems;
  if (!items.length) return;
  const nextIndex = Math.max(0, Math.min(index, items.length - 1));
  const changed = card._setIndex !== nextIndex;
  const item = items[nextIndex];
  card._setIndex = nextIndex;
  card._item = item;
  card.dataset.contentLinkId = String(item.content_link_id);
  card.querySelector("[data-set-position]").textContent = `${nextIndex + 1} / ${items.length}`;
  renderMeta(card.querySelector(".card-meta"), item);
  const score = card.querySelector('[data-count="vote-score"]');
  score.textContent = `${(item.vote_score || 0) >= 0 ? "+" : ""}${item.vote_score || 0}`;
  if (changed) setFeedbackMessage(card, "");
  card.querySelector('[data-set-nav="previous"]').disabled = nextIndex === 0;
  card.querySelector('[data-set-nav="next"]').disabled = nextIndex === items.length - 1;
}

function removeUnavailableSetItem(card, media, slide) {
  const items = card._setItems;
  const track = card._setTrack;
  const removedIndex = card._setMedia.indexOf(media);
  if (removedIndex < 0) return;
  const currentItem = card._item;
  card._setMedia.splice(removedIndex, 1);
  items.splice(removedIndex, 1);
  slide.remove();
  if (!items.length) {
    card.remove();
    return;
  }
  const currentIndex = items.indexOf(currentItem);
  const nextIndex = currentIndex >= 0 ? currentIndex : Math.min(removedIndex, items.length - 1);
  Array.from(track.children).forEach((child, i) => {
    child.setAttribute("aria-label", `${i + 1} of ${items.length}`);
  });
  activateSlide(card, nextIndex);
  track.scrollTo({ left: track.children[nextIndex].offsetLeft, behavior: "instant" });
}

function renderSetCard(contentSet) {
  const items = [...(contentSet.items || [])];
  const card = document.createElement("article");
  card.className = "card set-card";
  card._setItems = items;
  card._setMedia = [];
  card._setIndex = -1;

  const carousel = document.createElement("div");
  carousel.className = "set-carousel";
  const track = document.createElement("div");
  track.className = "set-track";
  track.setAttribute("aria-label", `${contentSet.label || "Content"} set`);
  items.forEach((item, index) => {
    const slide = document.createElement("div");
    slide.className = "set-slide";
    slide.setAttribute("aria-label", `${index + 1} of ${items.length}`);
    const media = createMedia(item, () => removeUnavailableSetItem(card, media, slide));
    card._setMedia.push(media);
    slide.appendChild(media.element);
    track.appendChild(slide);
  });
  carousel.appendChild(track);

  for (const [direction, symbol] of [["previous", "‹"], ["next", "›"]]) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `set-arrow set-arrow-${direction}`;
    button.dataset.setNav = direction;
    button.setAttribute("aria-label", `${direction === "previous" ? "Previous" : "Next"} link in set`);
    button.textContent = symbol;
    carousel.appendChild(button);
  }

  card.append(carousel, createSetBody(contentSet, items[0]));
  card._setTrack = track;

  let scrollFrame = null;
  track.addEventListener("scroll", () => {
    if (scrollFrame !== null) return;
    scrollFrame = window.requestAnimationFrame(() => {
      scrollFrame = null;
      activateSlide(card, Math.round(track.scrollLeft / (track.clientWidth || 1)));
    });
  }, { passive: true });
  return card;
}

function navigateSet(card, direction) {
  const nextIndex = card._setIndex + (direction === "next" ? 1 : -1);
  const slide = card.querySelectorAll(".set-slide")[nextIndex];
  if (!slide) return;
  const reducedMotion = reducedMotionQuery.matches;
  card._setTrack.scrollTo({ left: slide.offsetLeft, behavior: reducedMotion ? "auto" : "smooth" });
}

function cancelPendingMediaStarts() {
  pendingMediaStarts.forEach((timer) => window.clearTimeout(timer));
  pendingMediaStarts.clear();
}

function observeSetCard(card) {
  card._setMedia.forEach((media) => media.observe());
}

function scheduleSetMediaStart(card, delay) {
  const timer = window.setTimeout(() => {
    pendingMediaStarts.delete(timer);
    if (card.isConnected) observeSetCard(card);
  }, delay);
  pendingMediaStarts.add(timer);
}

function appendSetCards(contentSets, { staggerMedia = true } = {}) {
  const cards = contentSets.map((contentSet) => renderSetCard(contentSet));
  $("feed").append(...cards);
  cards.forEach((card) => activateSlide(card, 0));
  cards.forEach((card, index) => {
    if (!staggerMedia || index === 0) {
      observeSetCard(card);
      return;
    }
    scheduleSetMediaStart(
      card,
      FIRST_MEDIA_HEAD_START_MS + (index - 1) * MEDIA_STAGGER_MS,
    );
  });
}

function updateFeedback(card, payload) {
  const score = card.querySelector('[data-count="vote-score"]');
  score.textContent = `${payload.vote_score >= 0 ? "+" : ""}${payload.vote_score}`;
}

async function copyLink(card) {
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
    setFeedbackMessage(card, "link copied ♡");
    return true;
  } catch (_) {
    setFeedbackMessage(card, "couldn't copy automatically · use the source link");
    return false;
  }
}

async function handleFeedback(card, control) {
  const id = card.dataset.contentLinkId;
  const action = control.dataset.action;
  control.disabled = true;
  try {
    if (action === "copy") {
      const copied = await copyLink(card);
      if (copied) {
        const response = await fetch(`/api/feed/${id}/vote/up`, { method: "POST" });
        if (response.ok) updateFeedback(card, await response.json());
      }
      return;
    }
    const endpoint = action === "report"
      ? `/api/feed/${id}/report?reason=wrong_idol`
      : `/api/feed/${id}/vote/${action === "upvote" ? "up" : "down"}`;
    const response = await fetch(endpoint, { method: "POST" });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.detail || "That action could not be recorded.");
    updateFeedback(card, payload);
    setFeedbackMessage(card, action === "report" ? "thanks · wrong idol report recorded ♡" : "vote recorded ♡");
  } catch (error) {
    setFeedbackMessage(card, error.message || "That action could not be recorded.");
  } finally {
    control.disabled = false;
    control.blur();
  }
}

function isUrlLike(value) {
  return /^(https?:\/\/\S+|\S+\.\S+\/\S+)$/i.test(value.trim());
}

async function loadSets() {
  const navigationToken = ++state.navigationToken;
  $("query").blur();
  clearFeed();
  state.sets = [];
  state.nextCursor = null;
  state.requestParams = null;
  state.loadingMore = true;
  state.retryContinuation = false;
  setStatus("finding little sets…");

  const requestedSort = $("sort").value;
  const sort = filterOptions.sorts.includes(requestedSort) ? requestedSort : filterOptions.defaultSort;
  const query = $("query").value.trim();
  const urlMode = isUrlLike(query);
  state.requestParams = { limit: String(BATCH_SIZE), sort, query };
  let endpoint;
  if (urlMode) {
    endpoint = `/api/collections/by-url?url=${encodeURIComponent(query)}`;
  } else {
    const params = new URLSearchParams({ limit: String(BATCH_SIZE), sort });
    if (query) params.set("q", query);
    endpoint = `/api/sets?${params.toString()}`;
  }
  const submit = $("sets-form").querySelector('button[type="submit"]');
  submit.disabled = true;
  setSentinel("finding little sets…", "is-loading");
  try {
    const response = await fetch(endpoint);
    const payload = await response.json().catch(() => ({}));
    if (navigationToken !== state.navigationToken) return;
    if (!response.ok) throw new Error(payload.detail || "sets unavailable");
    state.sets = payload.sets || [];
    state.nextCursor = payload.next_cursor || null;
    state.retryContinuation = false;
    if (state.sets.length) {
      appendSetCards(state.sets);
      setStatus(`${state.sets.length} set${state.sets.length === 1 ? "" : "s"} loaded`);
    } else {
      const empty = document.createElement("p");
      empty.className = "empty";
      empty.textContent = urlMode
        ? "that link isn't in the library · sets form around ingested posts ♡"
        : "no little sets found · try another search ♡";
      $("feed").appendChild(empty);
      setStatus("0 sets");
    }
    setSentinel(state.nextCursor ? "" : "end of results");
    refreshSetSentinelObserver();
  } catch (error) {
    if (navigationToken !== state.navigationToken) return;
    const message = document.createElement("p");
    message.className = "empty";
    message.textContent = error.message || "sets unavailable · try again shortly";
    $("feed").appendChild(message);
    setSentinel("couldn't load sets · tap to retry", "is-error");
    setStatus(error.message || "something went wrong");
  } finally {
    if (navigationToken === state.navigationToken) {
      state.loadingMore = false;
      submit.disabled = false;
    }
  }
}

async function loadMoreSets() {
  if (!state.nextCursor || !state.requestParams || state.loadingMore) return;
  const navigationToken = state.navigationToken;
  const previousCursor = state.nextCursor;
  const params = new URLSearchParams({
    limit: state.requestParams.limit,
    sort: state.requestParams.sort,
    cursor: state.nextCursor,
  });
  if (state.requestParams.query) params.set("q", state.requestParams.query);
  state.loadingMore = true;
  setSentinel("finding more little sets…", "is-loading");
  setStatus("finding more sets…");
  try {
    const response = await fetch(`/api/sets?${params.toString()}`);
    const payload = await response.json().catch(() => ({}));
    if (navigationToken !== state.navigationToken) return;
    if (!response.ok) throw new Error(payload.detail || "more sets unavailable");
    state.nextCursor = payload.next_cursor || null;
    state.retryContinuation = false;
    const knownIds = new Set(state.sets.map((contentSet) => contentSet.collection_of));
    const incoming = (payload.sets || []).filter((contentSet) => !knownIds.has(contentSet.collection_of));
    if (!incoming.length) {
      if (state.nextCursor === previousCursor) state.nextCursor = null;
      setSentinel(state.nextCursor ? "" : "end of results");
      setStatus(`${state.sets.length} sets loaded${state.nextCursor ? "" : " · end of results"}`);
      refreshSetSentinelObserver();
      return;
    }
    state.sets.push(...incoming);
    appendSetCards(incoming);
    setStatus(`${state.sets.length} sets loaded`);
    setSentinel(state.nextCursor ? "" : "end of results");
    refreshSetSentinelObserver();
  } catch (error) {
    if (navigationToken !== state.navigationToken) return;
    state.retryContinuation = true;
    setSentinel("couldn't load more · tap to retry", "is-error");
    setStatus(error.message || "more sets unavailable");
  } finally {
    if (navigationToken === state.navigationToken) state.loadingMore = false;
  }
}

window.addEventListener("popstate", (event) => {
  state.navigationToken += 1;
  const nextHistoryKey = event.state?.viewKey || navigation.newKey();
  const snapshot = viewCache.get(nextHistoryKey);
  if (snapshot) viewCache.delete(nextHistoryKey);
  storeCurrentView();
  state.historyKey = nextHistoryKey;
  if (snapshot) restoreView(snapshot);
  else {
    window.scrollTo({ top: 0, behavior: "auto" });
    loadViewFromLocation();
  }
});

$("sets-form").addEventListener("submit", submitSetFilters);
$("sort").addEventListener("change", submitSetFilters);
$("timeline-search").addEventListener("click", focusSearch);
$("timeline-top").addEventListener("click", jumpToTop);
$("feed-sentinel").addEventListener("click", () => {
  if ($("feed-sentinel").classList.contains("is-error") && !state.retryContinuation) loadSets();
  else loadMoreSets();
});
window.addEventListener("scroll", scheduleTimelineToolsUpdate, { passive: true });
refreshSetSentinelObserver();
$("feed").addEventListener("click", (event) => {
  const titleFilter = event.target.closest("a[data-filter-query]");
  if (titleFilter) {
    if (!navigation.isPlainClick(event)) return;
    event.preventDefault();
    navigateSets({
      query: titleFilter.dataset.filterQuery,
      sort: $("sort").value,
    });
    return;
  }
  const setNavigation = event.target.closest("button[data-set-nav]");
  if (setNavigation) {
    const card = setNavigation.closest(".set-card");
    if (card) navigateSet(card, setNavigation.dataset.setNav);
    return;
  }
  const control = event.target.closest("button[data-action]");
  const card = control?.closest(".set-card");
  if (card) handleFeedback(card, control);
});

state.historyKey = window.history.state?.viewKey || navigation.newKey();
window.history.replaceState({ viewKey: state.historyKey }, "", window.location.href);
if ("scrollRestoration" in window.history) window.history.scrollRestoration = "manual";
loadViewFromLocation();
