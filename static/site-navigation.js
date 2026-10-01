/* Shared URL conventions: q for search, named keys for page-specific filters.
 * Keep filter state in URLs; keep media caches and private rankings out of them.
 */
(() => {
  "use strict";

  function currentUrl() {
    return new URL(window.location.href);
  }

  function readQuery(url = currentUrl()) {
    return (url.searchParams.get("q") ?? "").trim();
  }

  function readChoice(key, choices, fallback) {
    const value = currentUrl().searchParams.get(key);
    return choices.includes(value) ? value : fallback;
  }

  function readFilters({ sorts, defaultSort }) {
    return { query: readQuery(), sort: readChoice("sort", sorts, defaultSort) };
  }

  function url(values, { remove = [] } = {}) {
    const result = currentUrl();
    remove.forEach((key) => result.searchParams.delete(key));
    Object.entries(values).forEach(([key, value]) => {
      const text = String(value ?? "").trim();
      if (text) result.searchParams.set(key, text);
      else result.searchParams.delete(key);
    });
    return result;
  }

  function filterUrl(filters, options) {
    return url({ q: filters.query, ...(filters.sort !== undefined ? { sort: filters.sort } : {}) }, options);
  }

  function write(nextUrl, { replace = false } = {}) {
    if (nextUrl.href === window.location.href) return false;
    window.history[replace ? "replaceState" : "pushState"](window.history.state, "", nextUrl.href);
    return true;
  }

  function normalize({ sorts, defaultSort } = {}) {
    const current = currentUrl();
    const values = { q: readQuery(current) };
    if (sorts && current.searchParams.has("sort")) {
      values.sort = readChoice("sort", sorts, defaultSort);
    }
    write(url(values), { replace: true });
  }

  function newKey() {
    return window.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  }

  function isPlainClick(event) {
    return event.button === 0 && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey;
  }

  window.SiteNavigation = { readQuery, readChoice, readFilters, url, filterUrl, write, normalize, newKey, isPlainClick };
})();
