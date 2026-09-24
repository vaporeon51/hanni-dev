/* Render a consistent, compact share image independently of viewport size. */
async function createRankingImage(
  { entries, photoCount = 10, mode = "idols" },
) {
  const featured = entries.slice(0, photoCount);
  const remaining = entries.slice(photoCount);
  const width = 1000, margin = 40, gap = 16;
  const cardWidth = (width - margin * 2 - gap * 4) / 5;
  const photoHeight = cardWidth * 1.15;
  const cardHeight = photoHeight + 64;
  const photoRows = Math.ceil(featured.length / 5);
  const listTop = 110 + photoRows * (cardHeight + 16) +
    (featured.length && remaining.length ? 20 : 0);
  const listRowHeight = 54;
  const height = listTop + Math.ceil(remaining.length / 3) * listRowHeight + 66;
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
    `${entries.length} ${mode}`,
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
      ctx.fillStyle = colors.paper;
      rounded(x + 8, y + 8, 34, 30, 8);
      ctx.fill();
      ctx.textAlign = "center";
      text(entry.rank, x + 25, y + 29, "bold 16px Arial", colors.pink);
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
  const columnWidth = (width - margin * 2 - 24 * 2) / 3;
  remaining.forEach((entry, index) => {
    const x = margin + (index % 3) * (columnWidth + 24);
    const y = listTop + Math.floor(index / 3) * listRowHeight;
    text(entry.rank, x, y + 22, "18px Georgia", colors.pink);
    text(
      entry.name,
      x + 40,
      y + 19,
      "bold 14px Arial",
      colors.ink,
      columnWidth - 42,
    );
    text(
      entry.group,
      x + 40,
      y + 36,
      "11px Arial",
      colors.muted,
      columnWidth - 42,
    );
    ctx.strokeStyle = colors.line;
    ctx.beginPath();
    ctx.moveTo(x, y + 46);
    ctx.lineTo(x + columnWidth, y + 46);
    ctx.stroke();
  });
  ctx.textAlign = "center";
  text("bias sorter ♡", width / 2, height - 24, "18px Georgia", colors.pink);
  return canvas;
}
