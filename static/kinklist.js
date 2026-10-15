(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const STORAGE_KEY = "hanni-kinklist-v1";
  const RATINGS = [
    { id: "favorite", label: "Favorite", symbol: "♡", color: "#f2b1cc", ink: "#9b3e68" },
    { id: "like", label: "Like", symbol: "✓", color: "#a7cfbb", ink: "#386b55" },
    { id: "neutral", label: "Neutral", symbol: "−", color: "#c1b8c8", ink: "#655a70" },
    { id: "maybe", label: "Curious", symbol: "✧", color: "#d6b5ec", ink: "#79539b" },
    { id: "limit", label: "Limit", symbol: "×", color: "#f0b3a7", ink: "#9a4d3e" },
  ];
  const THEMES = {
    rose: { paper: "#fff5f8", surface: "#fffafb", accent: "#a14770", line: "#e8cbd8", text: "#543543", muted: "#8e687b", soft: "#f5dce7" },
    lilac: { paper: "#f5f1fc", surface: "#fcfaff", accent: "#78539a", line: "#dcd0eb", text: "#493b5c", muted: "#817093", soft: "#e8dff4" },
    mint: { paper: "#eff7f2", surface: "#fafffc", accent: "#46715d", line: "#c9dfd2", text: "#354e42", muted: "#6e887a", soft: "#dcece2" },
  };
  const symbols = { heart: "♡", sparkle: "✧", hand: "~", crown: "♧", moon: "☾" };
  let catalog = [], categoryId, history = [], previewTimer, noticeTimer, pendingImport;
  let imageURLs = [];
  let state = { version: 1, name: "", theme: "rose", answers: {}, custom: [], hideUnanswered: true, layout: "sheet" };
  const answerKey = (item, field) => `${item.id}:${field}`;
  const currentCategory = () => catalog.find((cat) => cat.id === categoryId);
  const itemsFor = (cat) => [...cat.items, ...state.custom.filter((item) => item.category === cat.id)];
  const allItems = () => catalog.flatMap((cat) => itemsFor(cat).map((item) => ({ cat, item })));
  const ratingFor = (id) => RATINGS.find((rating) => rating.id === id);

  function notice(message) {
    $("kl-notice").textContent = message;
    clearTimeout(noticeTimer);
    noticeTimer = setTimeout(() => { $("kl-notice").textContent = ""; }, 5000);
  }
  function save() {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
      $("kl-save-state").textContent = "♡ Saved in this browser";
    } catch {
      $("kl-save-state").textContent = "Storage unavailable · save a JSON backup";
    }
  }
  function remember() {
    history.push(JSON.stringify(state));
    if (history.length > 30) history.shift();
    $("kl-undo").disabled = false;
  }
  function changed({ render = false } = {}) {
    save();
    updateProgress();
    if (render) renderItems();
    schedulePreview();
  }
  function syncControls() {
    $("kl-name").value = state.name;
    $("kl-hide-unanswered").checked = state.hideUnanswered;
    $("kl-export-layout").value = state.layout;
    document.querySelectorAll("[data-theme]").forEach((button) => {
      button.setAttribute("aria-pressed", String(button.dataset.theme === state.theme));
    });
  }

  // Only known IDs and fields are accepted. Imported text is always rendered as text.
  function validate(raw) {
    if (!raw || typeof raw !== "object" || raw.version !== 1 || typeof raw.name !== "string" || raw.name.length > 40 ||
        !Object.hasOwn(THEMES, raw.theme) || !Array.isArray(raw.custom) || raw.custom.length > 100 ||
        !raw.answers || typeof raw.answers !== "object" || Array.isArray(raw.answers)) throw new Error("Invalid backup");
    const clean = { version: 1, name: raw.name, theme: raw.theme, answers: {}, custom: [],
      hideUnanswered: raw.hideUnanswered !== false, layout: raw.layout === "category" ? "category" : "sheet" };
    const ids = new Set(catalog.flatMap((cat) => cat.items.map((item) => item.id)));
    for (const item of raw.custom) {
      if (!item || typeof item.id !== "string" || !/^custom-[a-zA-Z0-9-]{1,80}$/.test(item.id) || ids.has(item.id) ||
          typeof item.name !== "string" || !item.name.trim() || item.name.length > 80 || !catalog.some((cat) => cat.id === item.category)) throw new Error("Invalid custom item");
      ids.add(item.id);
      clean.custom.push({ id: item.id, name: item.name.trim(), category: item.category });
    }
    const keys = new Set(catalog.flatMap((cat) => [...cat.items, ...clean.custom.filter((item) => item.category === cat.id)]
      .flatMap((item) => cat.fields.map((field) => answerKey(item, field)))));
    for (const [key, value] of Object.entries(raw.answers)) {
      if (!keys.has(key) || !ratingFor(value)) throw new Error("Unknown preference or rating");
      clean.answers[key] = value;
    }
    return clean;
  }
  function updateProgress() {
    let answered = 0, total = 0;
    catalog.forEach((cat) => {
      let count = 0;
      const items = itemsFor(cat);
      items.forEach((item) => cat.fields.forEach((field) => {
        total++;
        if (state.answers[answerKey(item, field)]) { answered++; count++; }
      }));
      const badge = document.querySelector(`[data-category="${cat.id}"] .kl-category-count`);
      if (badge) badge.textContent = `${count}/${items.length * cat.fields.length}`;
    });
    $("kl-progress").max = total;
    $("kl-progress").value = answered;
    $("kl-progress-count").textContent = `${answered} / ${total}`;
    $("kl-progress-label").textContent = answered === total ? "A lovely little map ♡" : answered ? "Taking shape" : "Getting started";
  }
  function renderCategories() {
    const nav = $("kl-categories");
    nav.replaceChildren();
    catalog.forEach((cat) => {
      const button = document.createElement("button");
      button.type = "button";
      button.dataset.category = cat.id;
      const icon = document.createElement("span");
      icon.textContent = symbols[cat.icon] || "✧";
      icon.setAttribute("aria-hidden", "true");
      const label = document.createElement("span");
      label.textContent = cat.name;
      const count = document.createElement("span");
      count.className = "kl-category-count";
      count.setAttribute("aria-hidden", "true");
      button.append(icon, label, count);
      button.addEventListener("click", () => selectCategory(cat.id));
      nav.append(button);
    });
  }
  function selectCategory(id, { scroll = false } = {}) {
    categoryId = id;
    $("kl-search").value = "";
    document.querySelectorAll("[data-category]").forEach((button) => {
      button.setAttribute("aria-current", String(button.dataset.category === id));
    });
    renderItems();
    schedulePreview();
    if (scroll) $("kl-category-title").scrollIntoView({ behavior: "instant", block: "start" });
  }
  function renderItems() {
    const cat = currentCategory();
    const query = $("kl-search").value.trim().toLowerCase();
    const filter = $("kl-filter").value;
    const entries = (query ? allItems() : itemsFor(cat).map((item) => ({ cat, item }))).filter(({ cat, item }) => {
      const answers = cat.fields.map((field) => state.answers[answerKey(item, field)]);
      return (!query || item.name.toLowerCase().includes(query)) && (filter === "all" ||
        (filter === "unanswered" ? answers.some((answer) => !answer) : answers.includes(filter)));
    });
    $("kl-category-number").textContent = query ? `${entries.length} FOUND IN YOUR COLLECTION` : `${String(catalog.indexOf(cat) + 1).padStart(2, "0")} / THE COLLECTION`;
    $("kl-category-title").textContent = query ? "A little search" : cat.name;
    $("kl-category-description").textContent = query ? "Matching preferences from every chapter." : cat.description;
    $("kl-custom-form").hidden = Boolean(query);
    const fragment = document.createDocumentFragment();
    entries.forEach(({ cat, item }) => {
      const article = document.createElement("article");
      article.className = "kl-item";
      article.id = `item-${item.id}`;
      const heading = document.createElement("div");
      heading.className = "kl-item-heading";
      const title = document.createElement("h3");
      title.textContent = item.name;
      heading.append(title);
      if (item.category) {
        const remove = document.createElement("button");
        remove.type = "button";
        remove.className = "kl-remove";
        remove.textContent = "Remove";
        remove.setAttribute("aria-label", `Remove custom preference ${item.name}`);
        remove.addEventListener("click", () => {
          remember();
          state.custom = state.custom.filter((entry) => entry.id !== item.id);
          cat.fields.forEach((field) => delete state.answers[answerKey(item, field)]);
          changed({ render: true });
          notice("Custom preference removed. Undo brings it back.");
        });
        heading.append(remove);
      }
      article.append(heading);
      cat.fields.forEach((field) => {
        const row = document.createElement("div");
        row.className = "kl-field";
        if (cat.fields.length > 1) {
          const label = document.createElement("span");
          label.className = "kl-field-label";
          label.textContent = field;
          row.append(label);
        }
        const choices = document.createElement("div");
        choices.className = "kl-choices";
        choices.setAttribute("role", "group");
        choices.setAttribute("aria-label", `${item.name} · ${field}. Select again to clear.`);
        RATINGS.forEach((rating) => {
          const button = document.createElement("button");
          button.className = "kl-choice";
          button.type = "button";
          button.style.setProperty("--rating-color", rating.color);
          button.dataset.rating = rating.id;
          button.setAttribute("aria-label", `${rating.label} · ${item.name} · ${field}`);
          button.setAttribute("aria-pressed", String(state.answers[answerKey(item, field)] === rating.id));
          const symbol = document.createElement("span");
          symbol.setAttribute("aria-hidden", "true");
          symbol.textContent = rating.symbol;
          button.append(symbol, document.createTextNode(rating.label));
          button.addEventListener("click", () => {
            remember();
            const key = answerKey(item, field);
            if (state.answers[key] === rating.id) delete state.answers[key];
            else state.answers[key] = rating.id;
            // Keep focus in place and update just this group on the normal path.
            choices.querySelectorAll("button").forEach((choice) => choice.setAttribute("aria-pressed", String(state.answers[key] === choice.dataset.rating)));
            changed({ render: filter !== "all" });
          });
          choices.append(button);
        });
        row.append(choices);
        article.append(row);
      });
      fragment.append(article);
    });
    if (!entries.length) {
      const empty = document.createElement("p");
      empty.className = "kl-empty";
      const heart = document.createElement("span");
      heart.textContent = "♡";
      empty.append(heart, document.createTextNode(query ? "No matches. Try another little search." : "Nothing here with this filter. Try All preferences."));
      fragment.append(empty);
    }
    $("kl-items").replaceChildren(fragment);
    $("kl-items").setAttribute("aria-busy", "false");
  }

  function schedulePreview() {
    clearTimeout(previewTimer);
    previewTimer = setTimeout(() => {
      try {
        const pages = makeCards({ preview: true });
        const source = pages[0];
        const canvas = $("kl-preview");
        // Preview uses a bounded bitmap, while exports retain full resolution.
        canvas.width = 600;
        canvas.height = Math.round(source.height * 600 / source.width);
        const ctx = canvas.getContext("2d");
        ctx.drawImage(source, 0, 0, canvas.width, canvas.height);
        canvas.setAttribute("aria-label", `Card preview for ${state.name || "My little kinklist"}, ${Object.keys(state.answers).length} answers. First image page.`);
      } catch { notice("The card preview is unavailable in this browser. You can still back up your answers."); }
    }, 100);
  }
  function wrap(ctx, text, maxWidth) {
    const lines = [];
    let line = "";
    // Character wrapping also handles long custom names without spaces.
    for (const char of text) {
      if (line && ctx.measureText(line + char).width > maxWidth) { lines.push(line); line = ""; }
      line += char;
    }
    if (line) lines.push(line);
    return lines.length ? lines : [""];
  }
  function rounded(ctx, x, y, w, h, r, color) {
    ctx.fillStyle = color;
    ctx.beginPath();
    ctx.roundRect(x, y, w, h, r);
    ctx.fill();
  }
  function makeCards({ preview = false } = {}) {
    const theme = THEMES[state.theme];
    const isCategory = state.layout === "category";
    const groups = (isCategory ? [currentCategory()] : catalog).map((cat) => ({ cat, items: itemsFor(cat).map((item) => ({
      item, fields: cat.fields.filter((field) => !state.hideUnanswered || state.answers[answerKey(item, field)]),
    })).filter((entry) => entry.fields.length) })).filter((group) => group.items.length);
    const itemCount = groups.reduce((sum, group) => sum + group.items.length, 0);
    const columns = isCategory || groups.length < 2 ? 1 : Math.min(3, groups.length);
    const width = columns === 1 ? 900 : columns === 2 ? 1180 : 1500;
    const margin = 48, gap = 22, top = 236, footer = 84, maxHeight = 2400;
    const colWidth = (width - margin * 2 - gap * (columns - 1)) / columns;
    const cardHeader = 78, cardBottom = 16;
    const capacity = maxHeight - top - footer;
    const scratch = document.createElement("canvas").getContext("2d");
    const totals = RATINGS.map((rating) => groups.reduce((sum, group) => sum + group.items.reduce((count, entry) =>
      count + entry.fields.filter((field) => state.answers[answerKey(entry.item, field)] === rating.id).length, 0), 0));

    // Measure first, then keep each category together. Only an oversized custom
    // category needs continuation panels; normal categories never straddle columns.
    const panels = [];
    groups.forEach(({ cat, items }) => {
      const badgeWidth = cat.fields.length > 1 ? 78 : 96;
      const ratingWidth = cat.fields.length * badgeWidth + (cat.fields.length - 1) * 6;
      const labelWidth = colWidth - 48 - ratingWidth - 14;
      const rows = items.map(({ item, fields }) => {
        scratch.font = "18px Arial";
        const lines = wrap(scratch, item.name, labelWidth);
        return { item, fields, lines, height: Math.max(44, lines.length * 23 + 16) };
      });
      let chunk = [], height = cardHeader + cardBottom, part = 0;
      const flush = () => {
        if (!chunk.length) return;
        panels.push({ cat, rows: chunk, height, badgeWidth, ratingWidth, part: part++ });
        chunk = []; height = cardHeader + cardBottom;
      };
      rows.forEach((row) => {
        if (height + row.height > capacity) flush();
        chunk.push(row); height += row.height;
      });
      flush();
    });
    const pages = [];
    let page = { panels: [], heights: Array(columns).fill(top) };
    panels.forEach((panel) => {
      let column = page.heights.indexOf(Math.min(...page.heights));
      if (page.heights[column] + panel.height > maxHeight - footer) {
        pages.push(page);
        page = { panels: [], heights: Array(columns).fill(top) };
        column = 0;
      }
      page.panels.push({ ...panel, x: margin + column * (colWidth + gap), y: page.heights[column] });
      page.heights[column] += panel.height + gap;
    });
    if (page.panels.length || !pages.length) pages.push(page);
    return (preview ? pages.slice(0, 1) : pages).map((page, pageIndex) => {
      const canvas = document.createElement("canvas");
      canvas.width = width;
      canvas.height = page.panels.length ? Math.max(540, Math.max(...page.heights) - gap + footer) : 660;
      const ctx = canvas.getContext("2d");
      ctx.textBaseline = "top";
      ctx.fillStyle = theme.paper;
      ctx.fillRect(0, 0, width, canvas.height);
      // An inset frame and a soft masthead keep the exported card distinct from
      // the editor without introducing external fonts, images, or network work.
      rounded(ctx, 16, 16, width - 32, canvas.height - 32, 24, theme.line);
      rounded(ctx, 18, 18, width - 36, canvas.height - 36, 22, theme.paper);
      rounded(ctx, 32, 32, width - 64, 128, 16, theme.soft);
      ctx.fillStyle = theme.accent;
      ctx.font = "13px monospace";
      ctx.fillText("HANNI  /  A LITTLE SELF-DISCOVERY", margin + 14, 51);
      ctx.font = "italic 52px Georgia";
      ctx.fillStyle = theme.text;
      ctx.fillText(state.name || "My little kinklist", margin + 12, 81, width - 286);
      // A tiny keepsake stamp: decorative, with useful collection size below it.
      ctx.textAlign = "center";
      ctx.fillStyle = theme.accent;
      // Draw the heart as a path so downloaded cards do not depend on glyph fallback.
      const hx = width - 121, hy = 65;
      ctx.beginPath();
      ctx.moveTo(hx, hy + 39);
      ctx.bezierCurveTo(hx - 8, hy + 32, hx - 24, hy + 21, hx - 24, hy + 10);
      ctx.bezierCurveTo(hx - 24, hy - 5, hx - 7, hy - 8, hx, hy + 4);
      ctx.bezierCurveTo(hx + 7, hy - 8, hx + 24, hy - 5, hx + 24, hy + 10);
      ctx.bezierCurveTo(hx + 24, hy + 21, hx + 8, hy + 32, hx, hy + 39);
      ctx.strokeStyle = theme.accent;
      ctx.lineWidth = 2.5;
      ctx.stroke();
      ctx.font = "12px monospace";
      ctx.fillText(`${itemCount} DETAILS · 18+`, width - 121, 121);
      ctx.font = "11px monospace";
      ctx.fillStyle = theme.muted;
      ctx.fillText(`${groups.length} ${groups.length === 1 ? "CATEGORY" : "CATEGORIES"}`, width - 121, 143);
      ctx.textAlign = "left";
      // A compact row of keepsake badges also serves as the rating legend.
      const badgeGap = 10;
      const summaryWidth = (width - margin * 2 - badgeGap * 4) / 5;
      RATINGS.forEach((rating, i) => {
        const x = margin + i * (summaryWidth + badgeGap);
        const compact = summaryWidth < 180;
        const label = [totals[i] === 1 ? "favorite" : "favorites", totals[i] === 1 ? "like" : "likes", "neutral", "curious", totals[i] === 1 ? "limit" : "limits"][i];
        const numberFont = `${compact ? 22 : 26}px Georgia`;
        const labelFont = `${compact ? 12 : 14}px Arial`;
        rounded(ctx, x, 176, summaryWidth, 44, 12, totals[i] ? `${rating.ink}0d` : theme.surface);
        ctx.font = numberFont;
        const numberWidth = ctx.measureText(String(totals[i])).width;
        ctx.font = labelFont;
        const contentWidth = 20 + 9 + numberWidth + 7 + ctx.measureText(label).width;
        const left = x + (summaryWidth - contentWidth) / 2;
        ctx.fillStyle = totals[i] ? rating.ink : theme.muted;
        ctx.font = "20px Arial";
        ctx.fillText(rating.symbol, left, 188);
        ctx.font = numberFont;
        ctx.fillText(String(totals[i]), left + 29, compact ? 185 : 183);
        ctx.font = labelFont;
        ctx.fillText(label, left + 29 + numberWidth + 7, compact ? 191 : 190);
      });
      if (!page.panels.length) {
        ctx.textAlign = "center";
        ctx.fillStyle = theme.accent;
        ctx.font = "72px Georgia";
        ctx.fillText("♡", width / 2, 290);
        ctx.font = "italic 34px Georgia";
        ctx.fillText("A little room for discovery.", width / 2, 402);
        ctx.fillStyle = theme.muted;
        ctx.font = "18px Arial";
        ctx.fillText("Your preferences will find their place here.", width / 2, 455);
        ctx.textAlign = "left";
      }
      page.panels.forEach((panel) => {
        const { cat, x, y, rows, badgeWidth, ratingWidth } = panel;
        rounded(ctx, x, y, colWidth, panel.height, 16, theme.line);
        rounded(ctx, x + 1, y + 1, colWidth - 2, panel.height - 2, 15, theme.surface);
        ctx.font = "italic 28px Georgia";
        ctx.fillStyle = theme.text;
        ctx.fillText(`${cat.name}${panel.part ? " · continued" : ""}`, x + 20, y + 20, colWidth - 40);
        const badgesX = x + colWidth - 20 - ratingWidth;
        cat.fields.forEach((field, i) => {
          ctx.font = "10px monospace";
          ctx.fillStyle = theme.muted;
          ctx.textAlign = "center";
          ctx.fillText(field === "General" ? "PREFERENCE" : field.toUpperCase(), badgesX + i * (badgeWidth + 6) + badgeWidth / 2, y + 57);
        });
        ctx.textAlign = "left";
        let rowY = y + cardHeader;
        rows.forEach((row, rowIndex) => {
          if (rowIndex % 2 === 0) rounded(ctx, x + 10, rowY, colWidth - 20, row.height, 7, theme.paper);
          ctx.fillStyle = theme.text;
          ctx.font = "18px Arial";
          const textY = rowY + (row.height - row.lines.length * 23) / 2 + 2;
          row.lines.forEach((line, i) => ctx.fillText(line, x + 20, textY + i * 23));
          cat.fields.forEach((field, i) => {
            // Preserve role columns even when one role is unanswered and hidden.
            const rating = ratingFor(state.answers[answerKey(row.item, field)]);
            if (!row.fields.includes(field)) return;
            const bx = badgesX + i * (badgeWidth + 6), by = rowY + (row.height - 26) / 2;
            rounded(ctx, bx, by, badgeWidth, 26, 13, rating ? `${rating.ink}14` : theme.soft);
            ctx.font = "13px Arial";
            ctx.fillStyle = rating ? rating.ink : theme.muted;
            ctx.textAlign = "center";
            ctx.fillText(rating ? rating.label : "—", bx + badgeWidth / 2, by + 5);
            ctx.textAlign = "left";
          });
          rowY += row.height;
        });
      });
      ctx.fillStyle = theme.line;
      ctx.fillRect(margin, canvas.height - 60, width - margin * 2, 1);
      ctx.fillStyle = theme.muted;
      ctx.textAlign = "right";
      ctx.font = "12px monospace";
      ctx.fillText(`HANNI ♡  /  ${pageIndex + 1} OF ${pages.length}`, width - margin, canvas.height - 43);
      ctx.textAlign = "left";
      return canvas;
    });
  }
  function download(blob, filename, { click = true } = {}) {
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    link.textContent = filename;
    if (click) {
      document.body.append(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 60000);
    } else imageURLs.push(url);
    return link;
  }
  async function exportCards() {
    if (state.hideUnanswered && !(state.layout === "category" ? itemsFor(currentCategory()).some((item) => currentCategory().fields.some((field) => state.answers[answerKey(item, field)])) : Object.keys(state.answers).length)) {
      notice("Choose a preference first, or turn off Hide unanswered.");
      return;
    }
    const button = $("kl-export");
    button.disabled = true;
    button.textContent = "Making something lovely…";
    try {
      // Yield so the button paints before rendering. No network or screenshot library.
      await new Promise((resolve) => requestAnimationFrame(resolve));
      const pages = makeCards();
      const blobs = await Promise.all(pages.map((canvas) => new Promise((resolve, reject) => canvas.toBlob((blob) => blob ? resolve(blob) : reject(new Error("Image encoding failed")), "image/png"))));
      imageURLs.forEach((url) => URL.revokeObjectURL(url));
      imageURLs = [];
      const links = blobs.map((blob, i) => download(blob, `hanni-kinklist-${state.layout === "category" ? categoryId : "collection"}-${i + 1}.png`, { click: false }));
      $("kl-downloads").replaceChildren(...links);
      $("kl-downloads").hidden = false;
      if (links.length === 1) links[0].click();
      notice(links.length === 1 ? "Your card is ready. Tap its link if the download didn’t start." : `${links.length} cards ready. Save each image using the links below.`);
    } catch {
      notice("Couldn’t create the image. Try a category card, or save a JSON backup.");
    } finally {
      button.disabled = false;
      button.replaceChildren(document.createTextNode("Save my card ↓"));
    }
  }
  function setup() {
    renderCategories();
    syncControls();
    selectCategory(catalog[0].id);
    updateProgress();
    $("kl-search").addEventListener("input", renderItems);
    $("kl-filter").addEventListener("change", renderItems);
    $("kl-name").addEventListener("input", () => { state.name = $("kl-name").value; changed(); });
    $("kl-hide-unanswered").addEventListener("change", () => { state.hideUnanswered = $("kl-hide-unanswered").checked; changed(); });
    $("kl-export-layout").addEventListener("change", () => { state.layout = $("kl-export-layout").value; changed(); });
    document.querySelectorAll("[data-theme]").forEach((button) => button.addEventListener("click", () => {
      state.theme = button.dataset.theme;
      syncControls();
      changed();
    }));
    $("kl-undo").addEventListener("click", () => {
      if (!history.length) return;
      state = JSON.parse(history.pop());
      $("kl-undo").disabled = !history.length;
      syncControls();
      changed({ render: true });
      notice("One little step back.");
    });
    $("kl-next").addEventListener("click", () => selectCategory(catalog[(catalog.findIndex((cat) => cat.id === categoryId) + 1) % catalog.length].id, { scroll: true }));
    $("kl-surprise").addEventListener("click", () => {
      const unanswered = allItems().filter(({ cat, item }) => cat.fields.some((field) => !state.answers[answerKey(item, field)]));
      if (!unanswered.length) { notice("Every preference has an answer. Your map is complete ♡"); return; }
      const { cat, item } = unanswered[Math.floor(Math.random() * unanswered.length)];
      $("kl-filter").value = "all";
      selectCategory(cat.id);
      const article = document.getElementById(`item-${item.id}`);
      article.scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth", block: "center" });
      article.querySelector("button.kl-choice").focus({ preventScroll: true });
      notice(`A little prompt: ${item.name}. Curious, certain, or still thinking?`);
    });
    $("kl-custom-form").addEventListener("submit", (event) => {
      event.preventDefault();
      const name = $("kl-custom-name").value.trim();
      if (!name) return;
      if (state.custom.length >= 100) { notice("Your collection has room for up to 100 custom preferences."); return; }
      remember();
      const item = { id: `custom-${crypto.randomUUID()}`, name, category: categoryId };
      state.custom.push(item);
      $("kl-custom-name").value = "";
      $("kl-filter").value = "all";
      changed({ render: true });
      document.getElementById(`item-${item.id}`).querySelector("button.kl-choice").focus();
      notice("A new little detail, just for you.");
    });
    $("kl-export").addEventListener("click", exportCards);
    $("kl-backup").addEventListener("click", () => {
      download(new Blob([JSON.stringify(state, null, 2)], { type: "application/json" }), "hanni-kinklist-backup.json");
      notice("Backup saved. It contains your private answers; keep it somewhere you trust.");
    });
    $("kl-import").addEventListener("click", () => $("kl-import-file").click());
    $("kl-import-file").addEventListener("change", async (event) => {
      const file = event.target.files[0];
      event.target.value = "";
      if (!file) return;
      try {
        if (file.size > 250000) throw new Error("Too large");
        pendingImport = validate(JSON.parse(await file.text()));
        $("kl-import-dialog").showModal();
      } catch { pendingImport = null; notice("That isn’t a valid Hanni kinklist backup. Your current collection is safe."); }
    });
    $("kl-import-cancel").addEventListener("click", () => { pendingImport = null; $("kl-import-dialog").close(); });
    $("kl-import-dialog").addEventListener("cancel", () => { pendingImport = null; });
    $("kl-import-confirm").addEventListener("click", () => {
      if (!pendingImport) return;
      remember();
      state = pendingImport;
      pendingImport = null;
      $("kl-import-dialog").close();
      syncControls();
      changed({ render: true });
      notice("Your collection is restored. Undo can bring the previous one back.");
    });
  }
  async function init() {
    try {
      const response = await fetch(document.body.dataset.catalog, { credentials: "same-origin" });
      if (!response.ok) throw new Error("Catalog unavailable");
      catalog = (await response.json()).categories;
      try {
        const saved = localStorage.getItem(STORAGE_KEY);
        if (saved) state = validate(JSON.parse(saved));
      } catch { notice("Saved answers could not be loaded. You can restore a JSON backup."); }
      setup();
    } catch {
      $("kl-category-description").textContent = "Your collection couldn’t load. Please refresh to try again.";
      $("kl-items").setAttribute("aria-busy", "false");
      ["kl-export", "kl-surprise", "kl-next", "kl-backup", "kl-import"].forEach((id) => { $(id).disabled = true; });
    }
  }
  init();
})();
