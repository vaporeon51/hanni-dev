/* Render a consistent, compact share image independently of viewport size. */
async function createRankingImage(
  { entries, photoCount = 10, mode = "idols" },
) {
  const medals = {
    1: { border: "#e8c96a", start: "#f8e7b2", end: "#edcc77", ink: "#806021", glow: "#c99a2e18" },
    2: { border: "#c4c7d0", start: "#f4f5f8", end: "#dce0e8", ink: "#606574", glow: "#79839814" },
    3: { border: "#d8b49c", start: "#f7e9df", end: "#e7c8b3", ink: "#805b43", glow: "#b9805716" },
  };
  const visibleEntries = entries.slice(0, 50);
  const featured = visibleEntries.slice(0, photoCount);
  const remaining = visibleEntries.slice(photoCount);
  const width = 1000, margin = 40, gap = 16;
  const cardWidth = (width - margin * 2 - gap * 4) / 5;
  const photoHeight = cardWidth * 1.15;
  const cardHeight = photoHeight + 64;
  const photoRows = Math.ceil(featured.length / 5);
  const listTop = 110 + photoRows * (cardHeight + 16) +
    (featured.length && remaining.length ? 20 : 0);
  const listRowHeight = 72;
  const height = listTop + Math.ceil(remaining.length / 4) * listRowHeight + 66;
  // Keep large lineups within browser canvas dimensions and a 16 MP budget.
  const scale = Math.min(
    2,
    Math.sqrt(16000000 / (width * height)),
    30000 / height,
  );
  const canvas = document.createElement("canvas");
  canvas.width = Math.floor(width * scale);
  canvas.height = Math.floor(height * scale);
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("Canvas unavailable");
  ctx.scale(scale, scale);
  const colors = {
    paper: "#fffafb",
    ink: "#49343e",
    muted: "#876b79",
    pink: "#b84d79",
    line: "#ecdde4",
    light: "#fbe8f0",
  };
  ctx.fillStyle = colors.paper;
  ctx.fillRect(0, 0, width, height);
  function text(value, x, y, font, color, maxWidth) {
    ctx.font = font;
    ctx.fillStyle = color;
    let label = String(value);
    if (maxWidth && ctx.measureText(label).width > maxWidth) {
      while (label.length && ctx.measureText(label + "…").width > maxWidth) {
        label = label.slice(0, -1);
      }
      label += "…";
    }
    ctx.fillText(label, x, y);
  }
  function rounded(x, y, w, h, radius) {
    ctx.beginPath();
    ctx.moveTo(x + radius, y);
    ctx.arcTo(x + w, y, x + w, y + h, radius);
    ctx.arcTo(x + w, y + h, x, y + h, radius);
    ctx.arcTo(x, y + h, x, y, radius);
    ctx.arcTo(x, y, x + w, y, radius);
    ctx.closePath();
  }
  text("My ranking ♡", margin, 61, "32px Georgia", colors.pink);
  ctx.textAlign = "right";
  text(
    entries.length > 50 ? `Top 50 of ${entries.length}` : `${entries.length} ${mode}`,
    width - margin,
    59,
    "14px Arial",
    colors.muted,
  );
  ctx.textAlign = "left";
  ctx.strokeStyle = colors.line;
  ctx.beginPath();
  ctx.moveTo(margin, 84);
  ctx.lineTo(width - margin, 84);
  ctx.stroke();
  function loadImage(source) {
    return new Promise((resolve) => {
      const image = new Image();
      image.crossOrigin = "anonymous";
      image.referrerPolicy = "no-referrer";
      const timeout = setTimeout(() => resolve(null), 10000);
      image.onload = () => {
        clearTimeout(timeout);
        resolve(image);
      };
      image.onerror = () => {
        clearTimeout(timeout);
        resolve(null);
      };
      image.src = source;
    });
  }
  // Load in small batches to keep exports of long lineups memory bounded.
  for (let start = 0; start < featured.length; start += 10) {
    const batch = featured.slice(start, start + 10);
    const images = await Promise.all(
      batch.map((entry) => loadImage(entry.image)),
    );
    batch.forEach((entry, offset) => {
      const medal = medals[entry.rank];
      const index = start + offset, image = images[offset];
      const x = margin + (index % 5) * (cardWidth + gap);
      const y = 110 + Math.floor(index / 5) * (cardHeight + 16);
      ctx.save();
      rounded(x, y, cardWidth, photoHeight, 12);
      ctx.clip();
      ctx.fillStyle = colors.light;
      ctx.fillRect(x, y, cardWidth, photoHeight);
      if (image) {
        const factor = Math.max(
          cardWidth / image.width,
          photoHeight / image.height,
        );
        const w = image.width * factor, h = image.height * factor;
        ctx.drawImage(
          image,
          x - (w - cardWidth) / 2,
          y - (h - photoHeight) * .2,
          w,
          h,
        );
      } else {text(
          "♡",
          x + cardWidth / 2 - 17,
          y + photoHeight / 2 + 13,
          "42px Georgia",
          colors.pink,
        );}
      ctx.restore();
      if (medal) {
        ctx.save();
        ctx.strokeStyle = medal.border;
        ctx.lineWidth = 1;
        ctx.shadowColor = medal.glow;
        ctx.shadowBlur = 16;
        ctx.shadowOffsetY = 5;
        rounded(x - 3.5, y - 3.5, cardWidth + 7, photoHeight + 7, 15.5);
        ctx.stroke();
        ctx.restore();
        ctx.save();
        ctx.strokeStyle = "#ffffff80";
        ctx.lineWidth = 1;
        rounded(x + .5, y + .5, cardWidth - 1, photoHeight - 1, 11.5);
        ctx.stroke();
        ctx.restore();
      }
      ctx.fillStyle = colors.paper;
      if (medal) {
        const gradient = ctx.createLinearGradient(x + 8, y + 8, x + 42, y + 38);
        gradient.addColorStop(0, medal.start);
        gradient.addColorStop(.48, "#fffaf2");
        gradient.addColorStop(1, medal.end);
        ctx.fillStyle = gradient;
      }
      rounded(x + 8, y + 8, 34, 30, 8);
      ctx.fill();
      if (medal) {
        ctx.strokeStyle = medal.border;
        ctx.stroke();
      }
      ctx.textAlign = "center";
      text(entry.rank, x + 25, y + 29, "bold 16px Arial", medal ? medal.ink : colors.pink);
      ctx.textAlign = "left";
      text(
        entry.name,
        x,
        y + photoHeight + 24,
        "bold 16px Arial",
        colors.ink,
        cardWidth,
      );
      text(
        entry.group,
        x,
        y + photoHeight + 44,
        "12px Arial",
        colors.muted,
        cardWidth,
      );
    });
  }
  const columnWidth = (width - margin * 2 - 20 * 3) / 4;
  for (let start = 0; start < remaining.length; start += 10) {
    const batch = remaining.slice(start, start + 10);
    const images = await Promise.all(batch.map((entry) => loadImage(entry.image)));
    batch.forEach((entry, offset) => {
      const medal = medals[entry.rank];
      const index = start + offset, image = images[offset];
      const x = margin + (index % 4) * (columnWidth + 20);
      const y = listTop + Math.floor(index / 4) * listRowHeight;
      text(entry.rank, x, y + 33, "18px Georgia", medal ? medal.ink : colors.pink);
      ctx.save();
      rounded(x + 30, y + 4, 44, 52, 8);
      ctx.clip();
      ctx.fillStyle = colors.light;
      ctx.fillRect(x + 30, y + 4, 44, 52);
      if (image) {
        const factor = Math.max(44 / image.width, 52 / image.height);
        const w = image.width * factor, h = image.height * factor;
        ctx.drawImage(image, x + 30 - (w - 44) / 2, y + 4 - (h - 52) * .2, w, h);
      } else {
        text("♡", x + 41, y + 38, "24px Georgia", colors.pink);
      }
      ctx.restore();
      if (medal) {
        ctx.save();
        ctx.strokeStyle = medal.border;
        ctx.lineWidth = 1;
        ctx.shadowColor = medal.glow;
        ctx.shadowBlur = 8;
        ctx.shadowOffsetY = 3;
        rounded(x + 27.5, y + 1.5, 49, 57, 10.5);
        ctx.stroke();
        ctx.restore();
      }
      text(
        entry.name,
        x + 84,
        y + 25,
        "bold 14px Arial",
        colors.ink,
        columnWidth - 84,
      );
      text(
        entry.group,
        x + 84,
        y + 43,
        "11px Arial",
        colors.muted,
        columnWidth - 84,
      );
      ctx.strokeStyle = "#ecdde480";
      ctx.beginPath();
      ctx.moveTo(x, y + 64);
      ctx.lineTo(x + columnWidth, y + 64);
      ctx.stroke();
    });
  }
  ctx.textAlign = "center";
  text("bias sorter ♡", width / 2, height - 24, "18px Georgia", colors.pink);
  return canvas;
}
