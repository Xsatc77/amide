// Keep the screen on while this page is open (the Screen Wake Lock API), so a phone does not lock mid-set and the sign-in is not
// left to time out: the page's own keep-alive only runs while the screen is awake. Needs a secure (https) address; where the browser
// has no wake lock it quietly does nothing.
(() => {
  if (!("wakeLock" in navigator)) return;
  let lock = null;
  const note = document.querySelector("[data-keep-awake-note]");

  async function acquire() {
    if (lock || document.visibilityState !== "visible") return;
    try {
      lock = await navigator.wakeLock.request("screen");
      lock.addEventListener("release", () => { lock = null; });
      if (note) note.textContent = "Your screen will stay on while this page is open.";
    } catch (error) {
      lock = null;                                  // not allowed yet (needs a tap) or not available; try again on the next tap
    }
  }

  acquire();
  document.addEventListener("visibilitychange", acquire);          // the lock is dropped when the page is hidden; take it again on return
  document.addEventListener("pointerdown", acquire, { passive: true });
  window.addEventListener("pagehide", () => { if (lock) lock.release(); });
})();
