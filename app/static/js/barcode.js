// Barcode lookup in the Add food dialog: scan with the camera, take a photo of the barcode, or type the digits, then look the code up in the USDA branded
// foods. The result is shown in the same list as a typed USDA search; picking it adds it to My foods and selects it.
//
// The camera reads a barcode by itself the moment one is sharp in view (the phone's own BarcodeDetector when it has one, otherwise the bundled ZXing in
// /static/vendor/zxing). Two things help when it does not: the Read it now button, which tries the picture on screen, and the Photo button, which uses the
// phone's own camera app (it focuses better) and reads the picture it takes. The camera needs a secure address (https); a photo and the typed code do not.
(() => {
  const box = document.querySelector("[data-barcode]");
  if (!box) return;
  const usda = document.querySelector("[data-usda-search]");
  const input = box.querySelector("[data-barcode-code]");
  const note = box.querySelector("[data-barcode-note]");
  const camera = box.querySelector("[data-barcode-camera]");
  const video = camera.querySelector("video");
  const photoInput = box.querySelector("[data-barcode-photo]");
  const FORMATS = ["ean_13", "ean_8", "upc_a", "upc_e"];
  let stream = null;
  let timer = null;
  let zxingReader = null;
  let finished = false;

  function say(text) { note.textContent = text; }

  function stopCamera() {
    if (timer) { clearInterval(timer); timer = null; }
    if (zxingReader) { try { zxingReader.reset(); } catch (error) { /* already stopped */ } zxingReader = null; }
    if (stream) stream.getTracks().forEach((t) => t.stop());
    stream = null;
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

  // A barcode was read: fill the box, close the camera and look it up.
  function found(text) {
    if (finished) return;
    const digits = String(text || "").replace(/\D/g, "");
    if (digits.length < 8 || digits.length > 14) return;
    finished = true;
    input.value = digits;
    stopCamera();
    say("Found " + digits + ". Looking it up…");
    if (navigator.vibrate) { try { navigator.vibrate(60); } catch (error) { /* not allowed */ } }
    lookup();
  }

  // ---- reading a still picture: a frame from the camera, or a photo the person took ----
  async function readImage(source) {
    if ("BarcodeDetector" in window) {
      try {
        const detector = new BarcodeDetector({ formats: FORMATS });
        const hits = await detector.detect(source);
        if (hits.length) return hits[0].rawValue;
      } catch (error) { /* fall through to ZXing */ }
    }
    if (window.ZXing) {
      try {
        const reader = new ZXing.BrowserMultiFormatReader(zxingHints());
        const canvas = document.createElement("canvas");
        const w = source.naturalWidth || source.videoWidth || source.width;
        const h = source.naturalHeight || source.videoHeight || source.height;
        const scale = Math.min(1, 1600 / Math.max(w, h));
        canvas.width = Math.round(w * scale);
        canvas.height = Math.round(h * scale);
        canvas.getContext("2d").drawImage(source, 0, 0, canvas.width, canvas.height);
        const image = new Image();
        image.src = canvas.toDataURL("image/png");
        await image.decode();
        const result = await reader.decodeFromImageElement(image);
        return result.getText();
      } catch (error) { /* no barcode in the picture */ }
    }
    return null;
  }

  function zxingHints() {
    const formats = [ZXing.BarcodeFormat.EAN_13, ZXing.BarcodeFormat.EAN_8, ZXing.BarcodeFormat.UPC_A, ZXing.BarcodeFormat.UPC_E];
    return new Map([[ZXing.DecodeHintType.POSSIBLE_FORMATS, formats], [ZXing.DecodeHintType.TRY_HARDER, true]]);
  }

  async function readNow() {
    if (!stream || !video.videoWidth) { say("The camera is not ready yet."); return; }
    say("Reading the picture…");
    const text = await readImage(video);
    if (text) found(text);
    else say("No barcode found in that picture. Hold the phone steady 6 to 10 inches away so the bars fill the view, then try again.");
  }

  async function readPhoto() {
    const file = photoInput.files && photoInput.files[0];
    photoInput.value = "";
    if (!file) return;
    say("Reading the photo…");
    const url = URL.createObjectURL(file);
    try {
      const image = new Image();
      image.src = url;
      await image.decode();
      finished = false;
      const text = await readImage(image);
      if (text) found(text);
      else say("No barcode found in that photo. Take it closer and in good light so the bars fill the picture, or type the digits under the barcode.");
    } catch (error) {
      say("That photo could not be read. Try again, or type the digits under the barcode.");
    } finally { URL.revokeObjectURL(url); }
  }

  // ---- the live camera ----
  async function scan() {
    if (!window.isSecureContext || !navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      say("The camera needs a secure (https) address. Use Photo, or type the digits under the barcode.");
      return;
    }
    finished = false;
    say("Starting the camera…");
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: { ideal: "environment" }, width: { ideal: 1920 }, height: { ideal: 1080 } }, audio: false,
      });
    } catch (error) {
      say("Could not use the camera (" + (error && error.name ? error.name : "blocked") + "). Allow camera access for this site, or use Photo or type the digits.");
      return;
    }
    video.muted = true;
    video.setAttribute("playsinline", "");
    video.srcObject = stream;
    camera.hidden = false;
    try { await video.play(); } catch (error) { /* autoplay can be refused until a tap: the Read it now button still works */ }
    try {                                                    // keep the picture sharp as the phone moves
      const track = stream.getVideoTracks()[0];
      const caps = track.getCapabilities ? track.getCapabilities() : {};
      if (caps.focusMode && caps.focusMode.includes("continuous")) await track.applyConstraints({ advanced: [{ focusMode: "continuous" }] });
    } catch (error) { /* not supported: carry on */ }
    say("Point the camera at the barcode and hold steady. It reads by itself; or press Read it now.");

    if ("BarcodeDetector" in window) {
      const detector = new BarcodeDetector({ formats: FORMATS });
      let busy = false;
      timer = setInterval(async () => {
        if (busy || finished || video.readyState < 2) return;
        busy = true;
        try { const hits = await detector.detect(video); if (hits.length) found(hits[0].rawValue); } catch (error) { /* a frame it could not read */ }
        busy = false;
      }, 200);
    } else if (window.ZXing) {
      zxingReader = new ZXing.BrowserMultiFormatReader(zxingHints(), 150);
      try {
        zxingReader.decodeFromVideoElement(video, (result) => { if (result) found(result.getText()); });
      } catch (error) { say("The scanner could not start. Use Photo, or type the digits under the barcode."); }
    } else {
      say("The scanner did not load. Use Photo, or type the digits under the barcode.");
    }
  }

  box.querySelector("[data-barcode-scan]").addEventListener("click", scan);
  box.querySelector("[data-barcode-stop]").addEventListener("click", () => { stopCamera(); say(""); });
  box.querySelector("[data-barcode-now]").addEventListener("click", readNow);
  box.querySelector("[data-barcode-lookup]").addEventListener("click", lookup);
  photoInput.addEventListener("change", readPhoto);
  input.addEventListener("keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); lookup(); } });
  const dialog = document.getElementById("food-dialog");
  if (dialog) dialog.addEventListener("close", stopCamera);
  window.addEventListener("pagehide", stopCamera);
})();
