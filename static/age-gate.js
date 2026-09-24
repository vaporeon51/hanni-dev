(() => {
  "use strict";
  const key = "hanni-18plus-acknowledged-v1";
  const loader = document.currentScript;
  const dialog = document.getElementById("age-gate");
  const form = document.getElementById("age-gate-form");
  let entered = false;

  function enter() {
    if (entered) return;
    entered = true;
    if (dialog.open) dialog.close();
    document.documentElement.classList.remove("age-gate-pending");
  }

  // Render the current page behind the modal; showModal keeps it inert.
  const script = document.createElement("script");
  script.src = loader.dataset.pageScript;
  document.body.appendChild(script);

  let acknowledged = false;
  try { acknowledged = localStorage.getItem(key) === "yes"; } catch {}
  // Keep acceptance through same-tab navigation if persistent storage is blocked.
  if (!acknowledged) {
    try { acknowledged = sessionStorage.getItem(key) === "yes"; } catch {}
  }
  if (acknowledged) {
    enter();
    return;
  }

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    try { localStorage.setItem(key, "yes"); } catch {}
    try { sessionStorage.setItem(key, "yes"); } catch {}
    enter();
  });
  dialog.addEventListener("cancel", (event) => {
    event.preventDefault();
    window.location.assign("/");
  });
  dialog.showModal();
})();
