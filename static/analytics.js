(() => {
  let countryCode = "XX";
  try {
    countryCode = new Intl.Locale(navigator.language).maximize().region || "XX";
  } catch (_) {
    const match = String(navigator.language || "").match(/[-_]([A-Za-z]{2})\b/);
    if (match) countryCode = match[1].toUpperCase();
  }
  fetch("/api/analytics/session", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ country_code: countryCode }),
    credentials: "same-origin",
    keepalive: true,
  }).catch(() => {});
})();
