(() => {
  'use strict';
  const el = id => document.getElementById(id);
  const sets = new Map();
  let queueData = null, current = null, navigation = 0;
  let previews = new AbortController();
  const message = text => { el('status').textContent = text; };
  async function api(path, options = {}) {
    const response = await fetch(`/api/disambiguation${path}`, {cache: 'no-store', ...options});
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || 'Request failed');
    return data;
  }
  const post = (path, body) => api(path, {method: 'POST', headers: {'Content-Type': 'application/json', 'X-Admin-Review': '1'}, ...(body ? {body: JSON.stringify(body)} : {})});
  // Limit metadata lookups for viewer/album links; direct photos need none.
  let active = 0;
  const pending = [];
  function schedule(task) {
    pending.push(task);
    drain();
  }
  function drain() {
    while (active < 3 && pending.length) {
      active++;
      Promise.resolve().then(pending.shift()).finally(() => { active--; drain(); });
    }
  }
  const playback = new IntersectionObserver(entries => {
    for (const entry of entries) {
      if (entry.isIntersecting) entry.target.play().catch(() => {});
      else entry.target.pause();
    }
  }, {threshold: 0.1});
  function releaseVideo(video) {
    playback.unobserve(video);
    video.onerror = null;
    video.onloadeddata = null;
    video.pause(); video.removeAttribute('src'); video.load();
  }
  function stopMedia() {
    previews.abort();
    previews = new AbortController();
    playback.disconnect();
    el('wall').querySelectorAll('video').forEach(releaseVideo);
    el('wall').replaceChildren();
  }
  const observer = new IntersectionObserver(entries => {
    for (const entry of entries) {
      if (!entry.isIntersecting) continue;
      observer.unobserve(entry.target);
      schedule(entry.target.loadPreview);
    }
  }, {rootMargin: '300px'});

  function updateProgress() {
    if (!current) return;
    const live = current.items.filter(item => !item.dead);
    const remaining = live.filter(item => !item.reviewed).length;
    el('progress').textContent = `${live.length} photos · ${remaining} left`;
  }
  function card(item, number) {
    const article = document.createElement('article');
    article.className = 'review-card';
    const wrap = document.createElement('div');
    wrap.className = 'card-media is-loading';
    const body = document.createElement('div'); body.className = 'card-body';
    const title = document.createElement('div'); title.className = 'card-title';
    const source = document.createElement('a');
    source.href = item.url; source.target = '_blank'; source.rel = 'noopener noreferrer'; source.textContent = 'original ↗';
    title.append(document.createTextNode(`Photo ${number}`), source);
    const form = document.createElement('form');
    form.setAttribute('aria-label', `Idol labels for photo ${number}`);
    const roles = document.createElement('div'); roles.className = 'card-roles';
    const inputs = item.roles.map(role => {
      const label = document.createElement('label');
      const input = document.createElement('input');
      input.type = 'checkbox'; input.value = role.id; input.checked = role.selected;
      label.title = role.label;
      label.append(input, document.createTextNode(role.name || role.label)); roles.append(label);
      return input;
    });
    const noneLabel = document.createElement('label'); noneLabel.className = 'none-choice';
    const none = document.createElement('input'); none.type = 'checkbox';
    none.checked = item.reviewed && !inputs.some(input => input.checked);
    noneLabel.append(none, document.createTextNode(' None of these idols'));
    none.onchange = () => { if (none.checked) inputs.forEach(input => { input.checked = false; }); };
    inputs.forEach(input => { input.onchange = () => { if (input.checked) none.checked = false; }; });
    const actions = document.createElement('div'); actions.className = 'card-actions';
    const save = document.createElement('button'); save.type = 'submit'; save.className = 'save'; save.textContent = 'Save';
    const broken = document.createElement('button'); broken.type = 'button'; broken.className = 'broken'; broken.textContent = 'Broken';
    actions.append(save, broken);
    const status = document.createElement('p'); status.className = 'card-status'; status.setAttribute('role', 'status');
    form.append(roles, noneLabel, actions, status);
    const previewStatus = document.createElement('p');
    previewStatus.className = 'preview-status';
    previewStatus.setAttribute('role', 'status');
    body.append(title, previewStatus, form); article.append(wrap, body);
    form.addEventListener('change', () => {
      article.classList.remove('saved'); status.textContent = 'Unsaved changes';
    });
    function lock(value) { [save, broken, none, ...inputs].forEach(input => { input.disabled = value; }); }
    form.onsubmit = async event => {
      event.preventDefault();
      const selected = inputs.filter(input => input.checked).map(input => input.value);
      if (!selected.length && !none.checked) { status.textContent = 'Choose an idol or “None of these idols”.'; return; }
      lock(true); status.textContent = 'Saving…';
      try {
        await post(`/${item.id}`, {selected, expected: item.roles.map(role => role.id)});
        item.reviewed = true;
        item.roles.forEach(role => { role.selected = selected.includes(role.id); });
        article.classList.add('saved'); status.textContent = 'Saved';
        queueData = null; updateProgress();
      } catch (error) { status.textContent = error.message; }
      finally { lock(false); }
    };
    broken.onclick = async () => {
      lock(true); status.textContent = 'Marking broken…';
      try {
        await post(`/${item.id}/broken`);
        item.dead = true; article.classList.add('dead'); status.textContent = 'Marked broken';
        observer.unobserve(wrap);
        wrap.querySelectorAll('video').forEach(releaseVideo);
        queueData = null; updateProgress();
      } catch (error) { status.textContent = error.message; }
      finally { if (!item.dead) lock(false); }
    };
    if (item.reviewed) { article.classList.add('saved'); status.textContent = 'Saved'; }
    wrap.loadPreview = async () => {
      if (!article.isConnected || item.dead) return;
      const signal = previews.signal;
      try {
        const preview = item.preview || await api(`/${item.id}/media`, {signal});
        if (!article.isConnected || item.dead || signal.aborted) return;
        if (!['image', 'video'].includes(preview.kind)) { wrap.className = 'card-media is-ready'; previewStatus.textContent = 'Open original to view this link.'; return; }
        const media = window.HanniMedia.create(preview.kind, `Photo ${number} awaiting review`);
        const ready = () => {
          if (!article.isConnected || item.dead || signal.aborted) return;
          wrap.className = 'card-media is-ready';
          if (preview.kind === 'video') playback.observe(media);
        };
        if (preview.kind === 'video') media.onloadeddata = ready;
        else media.onload = ready;
        let fallback = false;
        media.onerror = () => {
          if (!article.isConnected || item.dead || signal.aborted) return;
          if (preview.kind === 'image' && item.preview && !fallback) {
            fallback = true; media.src = `/api/disambiguation/${item.id}/asset`;
          } else { wrap.className = 'card-media is-ready'; previewStatus.textContent = 'Preview unavailable. Open original or mark broken.'; }
        };
        // Videos always use the same-origin stream, including Range requests.
        media.src = preview.kind === 'video' ? `/api/disambiguation/${item.id}/asset` : preview.url;
        wrap.append(media);
        if (preview.kind === 'video') media.load();
      } catch (error) {
        if (article.isConnected && !item.dead && !signal.aborted) {
          wrap.className = 'card-media is-ready'; previewStatus.textContent = error.message;
        }
      }
    };
    observer.observe(wrap);
    return article;
  }
  function renderQueue() {
    const query = el('search').value.trim().toLowerCase();
    el('set-list').replaceChildren();
    for (const set of queueData || []) {
      if (!set.key.toLowerCase().includes(query)) continue;
      const button = document.createElement('button'); button.className = 'set-choice';
      button.textContent = set.key;
      const detail = document.createElement('small');
      detail.textContent = `${set.reports} reports · ${set.size} photos · ${set.remaining} left · ${set.date ? new Date(set.date).toLocaleDateString() : 'date unknown'}`;
      button.append(detail); button.onclick = () => openSet(set.anchor_id, true);
      el('set-list').append(button);
    }
  }
  async function queue() {
    const version = ++navigation;
    observer.disconnect(); pending.length = 0; stopMedia();
    current = null; el('review').hidden = true; el('queue').hidden = false;
    try {
      if (!queueData) { message('Loading sets…'); queueData = (await api('')).sets; }
      if (version !== navigation) return;
      message(queueData.length ? '' : 'No sets awaiting review.'); renderQueue();
    } catch (error) { if (version === navigation) message(error.message); }
  }
  async function openSet(anchorId, push = false) {
    const version = ++navigation;
    message('Loading photos…');
    observer.disconnect(); pending.length = 0; stopMedia();
    try {
      if (!sets.has(String(anchorId))) {
        const result = await api(`/${anchorId}`);
        result.items = result.items.filter(item => !item.reviewed && item.roles.length > 1);
        sets.set(String(anchorId), result);
      }
      if (version !== navigation) return;
      current = sets.get(String(anchorId));
      if (push) history.pushState({reviewSet: anchorId}, '', `?set=${anchorId}`);
      el('queue').hidden = true; el('review').hidden = false;
      el('wall').replaceChildren(...current.items.filter(item => !item.dead).map((item, i) => card(item, i + 1)));
      updateProgress(); message(''); window.scrollTo(0, 0);
    } catch (error) { if (version === navigation) message(error.message); }
  }
  el('search').oninput = renderQueue;
  el('back').onclick = () => history.back();
  window.addEventListener('popstate', () => {
    const id = new URLSearchParams(location.search).get('set');
    if (id) openSet(id); else queue();
  });
  const initial = new URLSearchParams(location.search).get('set');
  history.replaceState({queue: true}, '', location.pathname);
  if (initial) openSet(initial, true); else queue();
})();
