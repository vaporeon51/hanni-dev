const BATCH_SIZE = 5;
const ALLOWED_SORTS = new Set(["latest", "oldest"]);
const MEDIA_PRELOAD_MARGIN = "550px 0px";
const FIRST_MEDIA_HEAD_START_MS = 360;
const MEDIA_STAGGER_MS = 110;
const MEDIA_RETRY_DELAYS_MS = [1500, 4000, 9000];
const state = {
  sets: [],
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
        media.alt = item.label || "Set item";
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
  const title = document.createElement("div");
  title.className = "card-title";
  title.textContent = contentSet.label || item.label || "content set";
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

function renderSetCard(contentSet) {
  const items = contentSet.items || [];
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
    const media = createMedia(item);
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

async function loadSets(event) {
  if (event) event.preventDefault();
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
  const sort = ALLOWED_SORTS.has(requestedSort) ? requestedSort : "latest";
  const query = $("query").value.trim();
  state.requestParams = { limit: String(BATCH_SIZE), sort, query };
  const params = new URLSearchParams({ limit: String(BATCH_SIZE), sort });
  if (query) params.set("query", query);
  const submit = $("sets-form").querySelector('button[type="submit"]');
  submit.disabled = true;
  setSentinel("finding little sets…", "is-loading");
  try {
    const response = await fetch(`/api/sets?${params.toString()}`);
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
      empty.textContent = "no little sets found · try another search ♡";
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
  if (state.requestParams.query) params.set("query", state.requestParams.query);
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

$("sets-form").addEventListener("submit", loadSets);
$("timeline-search").addEventListener("click", focusSearch);
$("timeline-top").addEventListener("click", jumpToTop);
$("feed-sentinel").addEventListener("click", () => {
  if ($("feed-sentinel").classList.contains("is-error") && !state.retryContinuation) loadSets();
  else loadMoreSets();
});
window.addEventListener("scroll", scheduleTimelineToolsUpdate, { passive: true });
refreshSetSentinelObserver();
$("feed").addEventListener("click", (event) => {
  const navigation = event.target.closest("button[data-set-nav]");
  if (navigation) {
    const card = navigation.closest(".set-card");
    if (card) navigateSet(card, navigation.dataset.setNav);
    return;
  }
  const control = event.target.closest("button[data-action]");
  const card = control?.closest(".set-card");
  if (card) handleFeedback(card, control);
});
