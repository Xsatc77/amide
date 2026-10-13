// Barcode lookup in the Add food dialog: scan with the camera (ZXing, bundled in /static/vendor/zxing) or type the digits, then look the code up
// in the USDA branded foods. The result is shown in the same list as a typed USDA search; picking it adds it to My foods and selects it.
// The camera needs a secure address (https) and a browser that allows it; the typed code works everywhere.
(() => {
  const box = document.querySelector("[data-barcode]");
  if (!box) return;
  const usda = document.querySelector("[data-usda-search]");
  const input = box.querySelector("[data-barcode-code]");
  const note = box.querySelector("[data-barcode-note]");
  const camera = box.querySelector("[data-barcode-camera]");
  const video = camera.querySelector("video");
  let reader = null;

  function say(text) { note.textContent = text; }

  function stopCamera() {
    if (reader) { try { reader.reset(); } catch (error) { /* already stopped */ } reader = null; }
    const stream = video.srcObject;
    if (stream && stream.getTracks) stream.getTracks().forEach((t) => t.stop());
    video.srcObject = null;
    camera.hidden = true;
  }

  async function lookup() {
    const code = input.value.replace(/\s+/g, "");
    if (!/^\d{8,14}$/.test(code)) { say("Enter the 8 to 14 digits printed under the barcode."); return; }
    say("Looking it up…");
    let response;
    try {
      response = await fetch("/food/barcode?code=" + encodeURIComponent(code), { credentials: "same-origin" });
    } catch (error) { say("Could not reach Amide. Check your connection."); return; }
    if (response.status === 404) { say("USDA search is not set up. Add a free key in Settings."); return; }
    if (response.status === 502) { say("The USDA database could not be reached. Try again."); return; }
    if (!response.ok) { say("That is not a barcode number."); return; }
    const foods = await response.json();
    say(foods.length ? "" : "That barcode is not in the USDA database. Add the food by hand with Create or Quick add.");
    usda.dispatchEvent(new CustomEvent("usda-results", { detail: { foods, message: "" } }));
  }

  async function scan() {
    if (!window.isSecureContext || !navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      say("The camera needs a secure (https) address. Type the digits under the barcode instead.");
      return;
    }
    if (!window.ZXing) { say("The scanner did not load. Type the digits under the barcode instead."); return; }
    say("");
    camera.hidden = false;
    const formats = [ZXing.BarcodeFormat.EAN_13, ZXing.BarcodeFormat.EAN_8, ZXing.BarcodeFormat.UPC_A, ZXing.BarcodeFormat.UPC_E];
    const hints = new Map([[ZXing.DecodeHintType.POSSIBLE_FORMATS, formats], [ZXing.DecodeHintType.TRY_HARDER, true]]);
    reader = new ZXing.BrowserMultiFormatReader(hints);
    try {
      await reader.decodeFromConstraints({ video: { facingMode: { ideal: "environment" } }, audio: false }, video, (result) => {
        if (!result) return;                       // no code in this frame yet: keep looking
        input.value = result.getText();
        stopCamera();
        lookup();
      });
    } catch (error) {
      stopCamera();
      say("Could not use the camera (" + (error && error.name ? error.name : "blocked") + "). Allow camera access for this site, or type the digits instead.");
    }
  }

  box.querySelector("[data-barcode-scan]").addEventListener("click", scan);
  box.querySelector("[data-barcode-stop]").addEventListener("click", stopCamera);
  box.querySelector("[data-barcode-lookup]").addEventListener("click", lookup);
  input.addEventListener("keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); lookup(); } });
  const dialog = document.getElementById("food-dialog");
  if (dialog) dialog.addEventListener("close", stopCamera);
  window.addEventListener("pagehide", stopCamera);
})();
