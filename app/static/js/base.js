// Runs on every page. It lives in a file (not inline in base.html) because the Content-Security-Policy allows scripts from this site only.
// Tell the server this device's time zone (a cookie), so "today" is the person's own day even when Settings is left on automatic.
try {
  const zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  if (zone && !document.cookie.split("; ").includes("amide_tz=" + zone)) document.cookie = "amide_tz=" + zone + "; path=/; max-age=31536000; SameSite=Lax";
} catch (e) {}
if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
// Keep the session alive while this tab is open; when the session has ended, start over at the notice.
setInterval(() => {
  fetch("/session/ping", { method: "POST" })
    .then((r) => { if (r.status === 401) location.href = "/notice"; })
    .catch(() => {});
}, 60000);
// A read-only field marked data-select-on-click selects its whole text when clicked (the calendar address), instead of an inline onclick.
document.querySelectorAll("input[data-select-on-click]").forEach((input) => input.addEventListener("click", () => input.select()));
