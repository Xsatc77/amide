// Amide's service worker. It exists so the browser offers to install Amide to the home screen; it never stores pages or data,
// because every page is the signed-in user's own health record. When the server cannot be reached it shows a short offline note.
const OFFLINE = "<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'><title>Amide</title>" +
  "<body style='font-family:system-ui;padding:2rem;background:#0f172a;color:#e2e8f0'><h1>Amide cannot be reached</h1>" +
  "<p>Check that the computer running Amide is on and that you are on the right network, then reload.</p></body>";

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));
self.addEventListener("fetch", (event) => {
  if (event.request.mode !== "navigate") return;                     // everything else goes straight to the network
  event.respondWith(fetch(event.request).catch(() => new Response(OFFLINE, { headers: { "Content-Type": "text/html; charset=utf-8" } })));
});
