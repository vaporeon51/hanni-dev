const BATCH_SIZE = 8;
const MAX_MOUNTED_REELS = 24;
const CLIENT_HISTORY_CAPACITY = 100;
const MEDIA_RETRY_DELAYS_MS = [1500, 4000, 9000];
const AUTOPLAY_STORAGE_KEY = "hanni-scroll-autoplay-ms";
const AUTOPLAY_DURATIONS_MS = [0, 5000, 10000, 20000];
const state = {
  cards: [],
  activeCard: null,
  query: "",
  loading: false,
  requestToken: 0,
  retryTimer: null,
  statusTimer: null,
  wheelLocked: false,
  wheelUnlockTimer: null,
  trimTimer: null,
  autoplayTimer: null,
  autoplayRaf: 0,
  autoplayStart: 0,
  autoplayDurationMs: 0,
  seenQueue: [],
  seenUrls: new Set(),
  spacer: null,
  spacerHeight: 0,
};

const $ = (id) => document.getElementById(id);
const dateFormatter = new Intl.DateTimeFormat(undefined, { dateStyle: "medium" });
const reducedMotionQuery = window.matchMedia("(prefers-reduced-motion: reduce)");

function formatDate(value) {
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  return dateFormatter.format(date);
}

function announce(text, { sticky = false } = {}) {
  const status = $("scroll-status");
  if (state.statusTimer !== null) window.clearTimeout(state.statusTimer);
  state.statusTimer = null;
  status.textContent = text;
  status.hidden = !text;
  if (text && !sticky) {
    state.statusTimer = window.setTimeout(() => {
      status.hidden = true;
      state.statusTimer = null;
    }, 1800);
  }
}

function rememberUrl(url) {
  if (state.seenUrls.has(url)) return false;
  state.seenUrls.add(url);
  state.seenQueue.push(url);
  while (state.seenQueue.length > CLIENT_HISTORY_CAPACITY) {
    state.seenUrls.delete(state.seenQueue.shift());
  }
  return true;
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

function itemFilterQuery(item) {
  const values = [item.member_name, item.group_name]
    .map((value) => String(value || "").trim())
    .filter(Boolean);
  return values.filter(
    (value, index) => values.findIndex((candidate) => candidate.toLocaleLowerCase() === value.toLocaleLowerCase()) === index,
  ).join(" ");
}

function createMedia(item, onResolved = () => {}) {
  const wrapper = document.createElement("div");
  wrapper.className = "reel-media is-loading";
  wrapper.textContent = "loading…";
  let resolved = null;
  let loading = false;
  let wantsLoaded = false;
  let wantsPlayback = false;
  let media = null;
  let fitObserver = null;
  let requestController = null;
  let retryTimer = null;
  let retryCount = 0;
  let disposed = false;

  const fitInsideStage = () => {
    if (!media) return;
    const intrinsicWidth = media.tagName === "VIDEO" ? media.videoWidth : media.naturalWidth;
    const intrinsicHeight = media.tagName === "VIDEO" ? media.videoHeight : media.naturalHeight;
    const availableWidth = wrapper.clientWidth;
    const availableHeight = wrapper.clientHeight;
    if (!intrinsicWidth || !intrinsicHeight || !availableWidth || !availableHeight) return;
    const scale = Math.min(
      availableWidth / intrinsicWidth,
      availableHeight / intrinsicHeight,
    );
    media.style.width = `${Math.floor(intrinsicWidth * scale)}px`;
    media.style.height = `${Math.floor(intrinsicHeight * scale)}px`;
  };

  const startFitting = () => {
    fitInsideStage();
    if ("ResizeObserver" in window && fitObserver === null) {
      fitObserver = new ResizeObserver(fitInsideStage);
      fitObserver.observe(wrapper);
    }
  };

  const cancelRetry = () => {
    if (retryTimer === null) return;
    window.clearTimeout(retryTimer);
    retryTimer = null;
  };

  const releaseMedia = () => {
    fitObserver?.disconnect();
    fitObserver = null;
    if (media?.tagName === "VIDEO") media.pause();
    if (media) {
      media.onerror = null;
      media.onload = null;
      media.onloadeddata = null;
      media.onloadedmetadata = null;
      media.removeAttribute("src");
      if (media.tagName === "VIDEO") media.load();
    }
    media = null;
  };

  const showSourceLink = () => {
    if (disposed || !wantsLoaded) return;
    cancelRetry();
    releaseMedia();
    const link = document.createElement("a");
    link.href = item.url;
    link.target = "_blank";
    link.rel = "noreferrer noopener";
    link.textContent = "open source link";
    wrapper.replaceChildren(link);
    wrapper.className = "reel-media is-link";
  };

  const scheduleRetry = (delay) => {
    if (disposed || !wantsLoaded) return;
    cancelRetry();
    releaseMedia();
    wrapper.replaceChildren();
    wrapper.className = "reel-media is-loading";
    wrapper.textContent = "media is catching up…";
    retryTimer = window.setTimeout(() => {
      retryTimer = null;
      if (wantsLoaded && !disposed) load();
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
    resolved = payload;
    if (disposed || !wantsLoaded) return;
    onResolved(payload);
    if (!resolved || !["video", "image"].includes(resolved.kind) || !resolved.url) {
      showSourceLink();
      return;
    }
    media = document.createElement(resolved.kind === "video" ? "video" : "img");
    media.referrerPolicy = "no-referrer";
    if (resolved.kind === "video") {
      media.controls = false;
      media.defaultMuted = true;
      media.loop = true;
      media.muted = true;
      media.preload = wantsPlayback ? "auto" : "metadata";
      media.playsInline = true;
      media.onloadedmetadata = startFitting;
    } else {
      media.alt = item.label || "Reel item";
      media.decoding = "async";
    }
    media.onerror = handleMediaError;
    const onReady = () => {
      startFitting();
      wrapper.className = "reel-media is-ready";
      if (resolved.kind === "video" && wantsPlayback) media.play().catch(() => {});
    };
    if (resolved.kind === "video") media.onloadeddata = onReady;
    else media.onload = onReady;
    wrapper.replaceChildren(media);
    media.src = resolved.url;
    if (resolved.kind === "video") media.load();
  };

  const load = async () => {
    wantsLoaded = true;
    if (disposed || loading || retryTimer !== null) return;
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
      if (error.isTransient && retryCount < MEDIA_RETRY_DELAYS_MS.length && wantsLoaded) {
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

  const play = () => {
    wantsPlayback = true;
    load();
    if (media?.tagName === "VIDEO") {
      media.preload = "auto";
      media.play().catch(() => {});
    }
  };

  const pause = () => {
    wantsPlayback = false;
    if (media?.tagName === "VIDEO") media.pause();
  };

  const unload = () => {
    wantsLoaded = false;
    wantsPlayback = false;
    cancelRetry();
    releaseMedia();
    wrapper.replaceChildren();
    wrapper.className = "reel-media is-loading";
    wrapper.textContent = "waiting…";
  };

  const dispose = () => {
    disposed = true;
    wantsLoaded = false;
    wantsPlayback = false;
    cancelRetry();
    requestController?.abort();
    requestController = null;
    releaseMedia();
  };

  return { element: wrapper, load, play, pause, unload, dispose };
}

function actionButton(action, symbol, label, { showLabel = true } = {}) {
  const group = document.createElement("div");
  group.className = "reel-action-group";
  const button = document.createElement("button");
  button.type = "button";
  button.className = "reel-action";
  button.dataset.action = action;
  button.setAttribute("aria-label", label);
  if (typeof symbol === "string") button.textContent = symbol;
  else button.appendChild(symbol);
  const caption = document.createElement("span");
  caption.className = "reel-action-label";
  caption.textContent = label;
  group.appendChild(button);
  if (showLabel) group.appendChild(caption);
  return group;
}

function thumbIcon(direction) {
  const namespace = "http://www.w3.org/2000/svg";
  const icon = document.createElementNS(namespace, "svg");
  icon.setAttribute("viewBox", "0 0 24 24");
  icon.setAttribute("aria-hidden", "true");
  if (direction === "down") icon.classList.add("is-downvote");
  const path = document.createElementNS(namespace, "path");
  path.setAttribute(
    "d",
    "M7 10v11H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3Zm0 0 4.1-7.1A2 2 0 0 1 12.83 2 2.17 2.17 0 0 1 15 4.17V8h4.35a2.65 2.65 0 0 1 2.55 3.38l-2.1 7.35A3.15 3.15 0 0 1 16.77 21H7",
  );
  icon.appendChild(path);
  return icon;
}

function createActions(item) {
  const actions = document.createElement("aside");
  actions.className = "reel-actions";
  actions.setAttribute("aria-label", "Link actions");

  const upvote = actionButton("upvote", thumbIcon("up"), "Upvote this link");
  const score = document.createElement("span");
  score.className = "reel-score";
  score.dataset.count = "vote-score";
  score.textContent = `${(item.vote_score || 0) >= 0 ? "+" : ""}${item.vote_score || 0}`;
  upvote.replaceChild(score, upvote.querySelector(".reel-action-label"));

  actions.append(
    upvote,
    actionButton("downvote", thumbIcon("down"), "Downvote this link", { showLabel: false }),
    actionButton("report", "!", "report"),
    actionButton("copy", "↗", "copy"),
  );
  return actions;
}

function autoplayEnabled() {
  return state.autoplayDurationMs > 0 && !reducedMotionQuery.matches;
}

function setProgressScale(bar, ratio) {
  if (!bar) return;
  bar.style.transform = `scaleX(${Math.min(1, Math.max(0, ratio))})`;
}

function clearAutoplayTimers() {
  if (state.autoplayTimer !== null) {
    window.clearTimeout(state.autoplayTimer);
    state.autoplayTimer = null;
  }
  if (state.autoplayRaf) {
    window.cancelAnimationFrame(state.autoplayRaf);
    state.autoplayRaf = 0;
  }
  if (state.activeCard) setProgressScale(state.activeCard._progressBar, 0);
}

function activeMediaReady(card) {
  const media = card?.querySelector(".reel-media");
  return Boolean(media) && !media.classList.contains("is-loading");
}

function tickAutoplayProgress() {
  state.autoplayRaf = 0;
  if (!autoplayEnabled() || !state.activeCard || document.hidden) return;
  if (!activeMediaReady(state.activeCard)) {
    state.autoplayStart = window.performance.now();
    setProgressScale(state.activeCard._progressBar, 0);
  } else {
    const elapsed = window.performance.now() - state.autoplayStart;
    setProgressScale(state.activeCard._progressBar, elapsed / state.autoplayDurationMs);
  }
  state.autoplayRaf = window.requestAnimationFrame(tickAutoplayProgress);
}

function advanceAutoplay() {
  state.autoplayTimer = null;
  if (!autoplayEnabled() || document.hidden) return;
  navigateBy(1);
  state.autoplayStart = window.performance.now();
  state.autoplayTimer = window.setTimeout(advanceAutoplay, state.autoplayDurationMs);
}

function scheduleAutoplay() {
  clearAutoplayTimers();
  if (!autoplayEnabled() || !state.activeCard || document.hidden) return;
  state.autoplayStart = window.performance.now();
  state.autoplayTimer = window.setTimeout(advanceAutoplay, state.autoplayDurationMs);
  state.autoplayRaf = window.requestAnimationFrame(tickAutoplayProgress);
}

function refreshAutoplayChrome() {
  const root = $("autoplay");
  if (!root) return;
  root.dataset.duration = String(state.autoplayDurationMs);
  const label = state.autoplayDurationMs === 0 ? "off" : `${Math.round(state.autoplayDurationMs / 1000)}s`;
  $("autoplay-label").textContent = label;
  $("autoplay-toggle").setAttribute("aria-label", `Autoplay next reel: ${label.toLowerCase()}`);
  root.querySelectorAll("#autoplay-menu button").forEach((option) => {
    option.setAttribute("aria-checked", String(Number(option.dataset.duration) === state.autoplayDurationMs));
  });
  const on = autoplayEnabled();
  state.cards.forEach((card) => {
    card.querySelector(".autoplay-progress")?.classList.toggle("is-on", on && card === state.activeCard);
  });
}

function setAutoplayDuration(durationMs, { silent = false } = {}) {
  if (!AUTOPLAY_DURATIONS_MS.includes(durationMs)) return;
  state.autoplayDurationMs = durationMs;
  try {
    window.localStorage.setItem(AUTOPLAY_STORAGE_KEY, String(durationMs));
  } catch (_) {}
  refreshAutoplayChrome();
  scheduleAutoplay();
  if (!silent) announce(durationMs === 0 ? "autoplay off" : `autoplay every ${Math.round(durationMs / 1000)} seconds`);
}

function initAutoplay() {
  const root = $("autoplay");
  if (!root) return;
  let stored = 0;
  try {
    stored = Number(window.localStorage.getItem(AUTOPLAY_STORAGE_KEY)) || 0;
  } catch (_) {}
  if (!AUTOPLAY_DURATIONS_MS.includes(stored)) stored = 0;
  state.autoplayDurationMs = reducedMotionQuery.matches ? 0 : stored;
  refreshAutoplayChrome();
  const toggle = $("autoplay-toggle");
  const menu = $("autoplay-menu");
  const closeMenu = () => {
    menu.hidden = true;
    toggle.setAttribute("aria-expanded", "false");
  };
  toggle.addEventListener("click", () => {
    const open = menu.hidden;
    menu.hidden = !open;
    toggle.setAttribute("aria-expanded", String(open));
    if (open) menu.querySelector('button[aria-checked="true"]')?.focus();
  });
  menu.addEventListener("click", (event) => {
    const option = event.target.closest("button[data-duration]");
    if (!option) return;
    setAutoplayDuration(Number(option.dataset.duration));
    closeMenu();
    toggle.focus();
  });
  document.addEventListener("click", (event) => {
    if (!menu.hidden && !root.contains(event.target)) closeMenu();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !menu.hidden) {
      closeMenu();
      toggle.focus();
    }
  });
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) clearAutoplayTimers();
    else scheduleAutoplay();
  });
  reducedMotionQuery.addEventListener?.("change", (event) => {
    if (event.matches) clearAutoplayTimers();
    refreshAutoplayChrome();
    scheduleAutoplay();
  });
}

function createReel(item) {
  const card = document.createElement("article");
  card.className = "reel";
  card.dataset.contentLinkId = String(item.content_link_id);
  card._item = item;

  const layout = document.createElement("div");
  layout.className = "reel-layout";
  const stage = document.createElement("div");
  stage.className = "reel-stage";
  const progress = document.createElement("div");
  progress.className = "autoplay-progress";
  progress.setAttribute("aria-hidden", "true");
  const progressBar = document.createElement("div");
  progressBar.className = "autoplay-progress-bar";
  progress.appendChild(progressBar);

  const caption = document.createElement("div");
  caption.className = "reel-caption";
  const title = document.createElement("button");
  title.type = "button";
  title.className = "reel-title";
  title.textContent = item.label || "untitled link";
  title.dataset.filterQuery = itemFilterQuery(item);
  title.setAttribute("aria-label", `Filter scroll to ${title.textContent}`);
  const titleRow = document.createElement("div");
  titleRow.className = "reel-title-row";
  const collectionLink = document.createElement("a");
  collectionLink.className = "reel-collection-link";
  collectionLink.hidden = true;
  collectionLink.href = `/?collection=${item.content_link_id}`;
  titleRow.append(title, collectionLink);
  const meta = document.createElement("div");
  meta.className = "reel-meta";
  renderMeta(meta, item);
  const media = createMedia(item, (payload) => {
    const count = Number(payload.collection_count) || 0;
    if (count < 2) return;
    collectionLink.textContent = `view set (${count}) →`;
    collectionLink.hidden = false;
  });
  stage.appendChild(media.element);
  stage.appendChild(progress);
  const message = document.createElement("p");
  message.className = "reel-message";
  message.setAttribute("aria-live", "polite");
  caption.append(titleRow, meta, message);
  stage.appendChild(caption);

  layout.append(stage, createActions(item));
  card.appendChild(layout);
  card._media = media;
  card._progressBar = progressBar;
  return card;
}

function setCardMessage(card, text) {
  card.querySelector(".reel-message").textContent = text;
}

function updateFeedback(card, payload) {
  const score = card.querySelector('[data-count="vote-score"]');
  if (score) score.textContent = `${payload.vote_score >= 0 ? "+" : ""}${payload.vote_score}`;
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
    setCardMessage(card, "link copied ♡");
    return true;
  } catch (_) {
    setCardMessage(card, "couldn't copy automatically");
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
    setCardMessage(card, action === "report" ? "wrong idol report recorded ♡" : "vote recorded ♡");
  } catch (error) {
    setCardMessage(card, error.message || "That action could not be recorded.");
  } finally {
    control.disabled = false;
    control.blur();
  }
}

function disposeCard(card) {
  reelObserver.unobserve(card);
  card._media.dispose();
  card.remove();
}

function trimMountedCards() {
  const activeIndex = state.cards.indexOf(state.activeCard);
  if (state.cards.length <= MAX_MOUNTED_REELS || activeIndex < 10) return;
  const removeCount = Math.min(8, activeIndex - 4);
  const removed = state.cards.slice(0, removeCount);
  const removedHeight = removed.reduce((height, card) => height + card.offsetHeight, 0);
  state.spacerHeight += removedHeight;
  state.spacer.style.height = `${state.spacerHeight}px`;
  removed.forEach(disposeCard);
  state.cards.splice(0, removeCount);
}

function scheduleMountedCardTrim() {
  if (state.trimTimer !== null) window.clearTimeout(state.trimTimer);
  state.trimTimer = window.setTimeout(() => {
    state.trimTimer = null;
    trimMountedCards();
  }, 900);
}

function setActiveCard(card) {
  if (!card || card === state.activeCard) return;
  state.activeCard?.classList.remove("is-active");
  state.activeCard?._media.pause();
  state.activeCard = card;
  card.classList.add("is-active");
  const on = autoplayEnabled();
  state.cards.forEach((candidate) => {
    candidate.querySelector(".autoplay-progress")?.classList.toggle("is-on", on && candidate === card);
  });
  const index = state.cards.indexOf(card);
  state.cards.forEach((candidate, candidateIndex) => {
    const distance = Math.abs(candidateIndex - index);
    if (candidate === card) candidate._media.play();
    else if (distance <= 1) {
      candidate._media.pause();
      candidate._media.load();
    } else {
      candidate._media.unload();
    }
  });
  if (index >= state.cards.length - 3) loadMore();
  scheduleMountedCardTrim();
  scheduleAutoplay();
}

const reelObserver = new IntersectionObserver((entries) => {
  const visible = entries
    .filter((entry) => entry.isIntersecting && entry.intersectionRatio >= 0.55)
    .sort((left, right) => right.intersectionRatio - left.intersectionRatio);
  if (visible[0]) setActiveCard(visible[0].target);
}, {
  root: $("reel-feed"),
  threshold: [0.55, 0.7, 0.9],
});

function appendItems(items) {
  const feed = $("reel-feed");
  const fragment = document.createDocumentFragment();
  const addedCards = [];
  items.forEach((item) => {
    if (!item?.url || !rememberUrl(item.url)) return;
    const card = createReel(item);
    state.cards.push(card);
    addedCards.push(card);
    fragment.appendChild(card);
  });
  feed.appendChild(fragment);
  addedCards.forEach((card) => reelObserver.observe(card));
  return addedCards.length;
}

async function loadMore({ initial = false } = {}) {
  if (state.loading) return;
  state.loading = true;
  const token = state.requestToken;
  if (initial) announce("finding little reels…", { sticky: true });
  const params = new URLSearchParams({ limit: String(BATCH_SIZE) });
  if (state.query) params.set("query", state.query);

  try {
    const response = await fetch(`/api/scroll?${params.toString()}`);
    const payload = await response.json().catch(() => ({}));
    if (token !== state.requestToken) return;
    if (response.status === 429) {
      const delay = Math.max(1, Number(response.headers.get("Retry-After")) || 1) * 1000;
      announce("finding the next reel…");
      state.retryTimer = window.setTimeout(() => loadMore({ initial }), delay);
      return;
    }
    if (!response.ok) throw new Error(payload.detail || "reels unavailable");
    const batchItems = payload.items || [];
    if (payload.cycle_reset) {
      state.seenQueue = [];
      state.seenUrls.clear();
    }
    let added = appendItems(batchItems);
    if (added === 0 && batchItems.length) {
      announce("looking for something new…");
      state.retryTimer = window.setTimeout(() => loadMore(), 1100);
    }
    if (initial && state.cards.length) {
      const first = state.cards[0];
      $("reel-feed").scrollTo({ top: 0, behavior: "auto" });
      setActiveCard(first);
      first._media.load();
      state.cards[1]?._media.load();
      announce("");
    } else if (initial && !state.cards.length) {
      announce("no reels found · try another search ♡", { sticky: true });
    } else if (added === 0) {
      announce("looking for something new…");
    }
  } catch (error) {
    if (token !== state.requestToken) return;
    announce(error.message || "reels unavailable · try again shortly", { sticky: state.cards.length === 0 });
  } finally {
    if (token === state.requestToken) state.loading = false;
  }
}

function resetFeed(query) {
  state.requestToken += 1;
  if (state.retryTimer !== null) window.clearTimeout(state.retryTimer);
  state.retryTimer = null;
  if (state.trimTimer !== null) window.clearTimeout(state.trimTimer);
  state.trimTimer = null;
  if (state.wheelUnlockTimer !== null) window.clearTimeout(state.wheelUnlockTimer);
  state.wheelUnlockTimer = null;
  state.wheelLocked = false;
  state.loading = false;
  state.activeCard = null;
  clearAutoplayTimers();
  state.query = query;
  state.seenQueue = [];
  state.seenUrls.clear();
  state.spacerHeight = 0;
  state.cards.forEach(disposeCard);
  state.cards = [];
  state.spacer = document.createElement("div");
  state.spacer.className = "reel-spacer";
  state.spacer.setAttribute("aria-hidden", "true");
  $("reel-feed").replaceChildren(state.spacer);
  const url = new URL(window.location.href);
  if (query) url.searchParams.set("q", query);
  else url.searchParams.delete("q");
  window.history.replaceState(
    { ...(window.history.state || {}), scrollQuery: query },
    "",
    url,
  );
  loadMore({ initial: true });
}

function navigateBy(direction) {
  const index = Math.max(0, state.cards.indexOf(state.activeCard));
  const nextIndex = index + direction;
  const target = state.cards[nextIndex];
  if (!target) {
    if (direction > 0) loadMore();
    return;
  }
  const reducedMotion = reducedMotionQuery.matches;
  $("reel-feed").scrollTo({
    top: target.offsetTop,
    behavior: reducedMotion ? "auto" : "smooth",
  });
}

$("scroll-form").addEventListener("submit", (event) => {
  event.preventDefault();
  $("query").blur();
  resetFeed($("query").value.trim());
});

$("reel-feed").addEventListener("click", (event) => {
  const titleFilter = event.target.closest("button[data-filter-query]");
  if (titleFilter) {
    const query = titleFilter.dataset.filterQuery.trim();
    if (query) {
      $("query").value = query;
      resetFeed(query);
    }
    return;
  }
  const control = event.target.closest("button[data-action]");
  const card = control?.closest(".reel");
  if (card) handleFeedback(card, control);
  if (card) scheduleAutoplay();
});

$("reel-feed").addEventListener("wheel", (event) => {
  if (!window.matchMedia("(hover: hover) and (pointer: fine)").matches) return;
  if (Math.abs(event.deltaX) > Math.abs(event.deltaY)) return;
  // Always suppress native vertical movement on desktop. Small trackpad tail
  // deltas must not nudge the feed after the one-reel animation has settled.
  event.preventDefault();
  if (Math.abs(event.deltaY) < 12) return;
  const gestureAlreadyHandled = state.wheelLocked;
  state.wheelLocked = true;
  if (state.wheelUnlockTimer !== null) window.clearTimeout(state.wheelUnlockTimer);
  state.wheelUnlockTimer = window.setTimeout(() => {
    state.wheelLocked = false;
    state.wheelUnlockTimer = null;
  }, 720);
  if (gestureAlreadyHandled) return;
  navigateBy(event.deltaY > 0 ? 1 : -1);
}, { passive: false });

document.addEventListener("keydown", (event) => {
  if (event.target.matches("input, button, a")) return;
  if (event.repeat) return;
  if (["ArrowDown", "PageDown", " "].includes(event.key)) {
    event.preventDefault();
    navigateBy(1);
  } else if (["ArrowUp", "PageUp"].includes(event.key)) {
    event.preventDefault();
    navigateBy(-1);
  }
});

function restoredQuery() {
  const urlQuery = new URL(window.location.href).searchParams.get("q");
  if (urlQuery !== null) return urlQuery.trim();
  const historyQuery = window.history.state?.scrollQuery;
  if (typeof historyQuery === "string") return historyQuery.trim();
  return state.query;
}

window.addEventListener("pageshow", () => {
  // Safari may clear autocomplete-off fields when restoring from its
  // back-forward cache even though the filtered URL and reel state survive.
  const restoreInput = () => { $("query").value = restoredQuery(); };
  restoreInput();
  window.requestAnimationFrame(restoreInput);
});

initAutoplay();
const initialQuery = restoredQuery();
$("query").value = initialQuery;
resetFeed(initialQuery);
