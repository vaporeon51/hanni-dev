const ALLOWED_LIMITS = new Set([1, 15, 30]);
const ALLOWED_SORTS = new Set(["random", "latest", "oldest", "top"]);
const REVEAL_DELAY_MS = 2000;
const state = {
  sets: [],
  revealTimer: null,
  revealToken: 0,
  visibleCount: 0,
  navigationToken: 0,
};

const $ = (id) => document.getElementById(id);

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
        if (entry.isIntersecting) entry.target.play().catch(() => {});
        else entry.target.pause();
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
  progress.hidden = !active;
  progress.classList.remove("is-counting");
  if (active) {
    void progress.offsetWidth;
    progress.classList.add("is-counting");
  }
  progress.closest(".feed-status").hidden = !active && $("status").hidden;
}

function setControlsVisible({ stop = false, jumps = false, floating = false } = {}) {
  $("stop-feed").hidden = !stop;
  $("jump-top").hidden = !jumps;
  $("jump-bottom").hidden = !jumps;
  $("status").closest(".feed-status").classList.toggle("is-floating", floating);
}

function cancelReveal() {
  if (state.revealTimer !== null) window.clearTimeout(state.revealTimer);
  state.revealTimer = null;
  state.revealToken += 1;
  setRevealProgress(false);
  $("stop-feed").hidden = true;
}

function clearFeed() {
  const feed = $("feed");
  feed.querySelectorAll("video").forEach((video) => {
    if (videoPlaybackObserver) videoPlaybackObserver.unobserve(video);
    video.pause();
  });
  feed.replaceChildren();
  setControlsVisible();
}

function formatDate(value) {
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium" }).format(date);
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
  let started = false;

  const showSourceLink = () => {
    wrapper.replaceChildren(externalLink(item));
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
      media.alt = item.label || "Set item";
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
  card._setMedia[nextIndex].load();
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
  card._pendingMediaLoad = () => activateSlide(card, 0);

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
  card._setMedia[nextIndex].load();
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  card._setTrack.scrollTo({ left: slide.offsetLeft, behavior: reducedMotion ? "auto" : "smooth" });
}

function revealNext(token) {
  if (token !== state.revealToken || state.visibleCount >= state.sets.length) return;
  const card = renderSetCard(state.sets[state.visibleCount]);
  $("feed").appendChild(card);
  card._pendingMediaLoad();
  state.visibleCount += 1;
  setControlsVisible({
    stop: state.visibleCount < state.sets.length,
    jumps: true,
    floating: true,
  });

  if (state.visibleCount < state.sets.length) {
    setStatus(`showing ${state.visibleCount} of ${state.sets.length} · next set in 2 seconds`);
    setRevealProgress(true);
    state.revealTimer = window.setTimeout(() => revealNext(token), REVEAL_DELAY_MS);
  } else {
    state.revealTimer = null;
    setRevealProgress(false);
    setStatus(`${state.visibleCount} set${state.visibleCount === 1 ? "" : "s"} shown`);
  }
}

function renderSets() {
  cancelReveal();
  clearFeed();
  if (!state.sets.length) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = "no little sets found · try another search ♡";
    $("feed").appendChild(empty);
    setStatus("0 sets");
    return;
  }
  state.visibleCount = 0;
  revealNext(state.revealToken);
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
  cancelReveal();
  clearFeed();
  state.sets = [];
  state.visibleCount = 0;
  setStatus("finding little sets…");

  const requestedLimit = Number($("limit").value);
  const requestedSort = $("sort").value;
  const params = new URLSearchParams({
    limit: String(ALLOWED_LIMITS.has(requestedLimit) ? requestedLimit : 15),
    sort: ALLOWED_SORTS.has(requestedSort) ? requestedSort : "random",
  });
  const query = $("query").value.trim();
  if (query) params.set("query", query);
  const submit = $("sets-form").querySelector('button[type="submit"]');
  submit.disabled = true;
  try {
    const response = await fetch(`/api/sets?${params.toString()}`);
    const payload = await response.json().catch(() => ({}));
    if (navigationToken !== state.navigationToken) return;
    if (!response.ok) throw new Error(payload.detail || "sets unavailable");
    state.sets = payload.sets || [];
    renderSets();
  } catch (error) {
    if (navigationToken !== state.navigationToken) return;
    const message = document.createElement("p");
    message.className = "empty";
    message.textContent = error.message || "sets unavailable · try again shortly";
    $("feed").appendChild(message);
    setStatus(error.message || "something went wrong");
  } finally {
    submit.disabled = false;
  }
}

function stopFeed() {
  if (state.revealTimer === null) return;
  cancelReveal();
  setControlsVisible({ jumps: state.visibleCount > 0, floating: state.visibleCount > 0 });
  setStatus(`stopped at ${state.visibleCount} of ${state.sets.length}`);
}

function jumpTo(direction) {
  const target = direction === "top"
    ? $("sets-form")
    : $("feed").querySelector(".card:last-child");
  if (!target) return;
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  target.scrollIntoView({ behavior: reducedMotion ? "auto" : "smooth", block: direction === "top" ? "start" : "end" });
}

$("sets-form").addEventListener("submit", loadSets);
$("stop-feed").addEventListener("click", stopFeed);
$("jump-top").addEventListener("click", () => jumpTo("top"));
$("jump-bottom").addEventListener("click", () => jumpTo("bottom"));
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
