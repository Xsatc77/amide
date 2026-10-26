// Passkeys: sign in with Face ID, a fingerprint or a device PIN (login page), and add one (Passkeys page).
// The browser and the device do the work; this only carries the challenge and the signed answer to and from the server.
(() => {
  const toBytes = (text) => {
    const padded = text.replace(/-/g, "+").replace(/_/g, "/").padEnd(Math.ceil(text.length / 4) * 4, "=");
    return Uint8Array.from(atob(padded), (c) => c.charCodeAt(0));
  };
  const toText = (buffer) => {
    const bytes = new Uint8Array(buffer);
    let binary = "";
    bytes.forEach((b) => { binary += String.fromCharCode(b); });
    return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  };
  const post = async (url, body) => {
    const response = await fetch(url, {
      method: "POST", headers: { "Content-Type": "application/json" }, credentials: "same-origin",
      body: body === undefined ? "{}" : JSON.stringify(body),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || "That did not work. Try again.");
    return data;
  };
  const show = (box, message) => { box.textContent = message; box.hidden = !message; };
  const supported = () => !!(window.PublicKeyCredential && navigator.credentials);

  // ---- Login page: the "Sign in with a passkey" button
  const loginButton = document.querySelector("[data-passkey-login]");
  if (loginButton) {
    document.querySelector("[data-passkey-box]").hidden = !supported();          // only browsers that can do passkeys see the button
    loginButton.addEventListener("click", async () => {
      const box = document.querySelector("[data-passkey-error]");
      show(box, "");
      try {
        const options = await post("/login/passkey/options");
        options.challenge = toBytes(options.challenge);
        (options.allowCredentials || []).forEach((c) => { c.id = toBytes(c.id); });
        const credential = await navigator.credentials.get({ publicKey: options });
        const answer = await post("/login/passkey", {
          credential: {
            id: credential.id, rawId: toText(credential.rawId), type: credential.type,
            response: {
              authenticatorData: toText(credential.response.authenticatorData),
              clientDataJSON: toText(credential.response.clientDataJSON),
              signature: toText(credential.response.signature),
              userHandle: credential.response.userHandle ? toText(credential.response.userHandle) : null,
            },
          },
        });
        window.location.href = answer.next;
      } catch (error) {
        if (error && error.name === "NotAllowedError") return;          // the person cancelled the prompt
        show(box, error.message || "That passkey did not work. Use your password instead.");
      }
    });
  }

  // ---- Passkeys page: add one on this device
  const addForm = document.querySelector("[data-passkey-add]");
  if (addForm) {
    const box = document.querySelector("[data-passkey-error]");
    if (!supported()) addForm.hidden = true;
    addForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      show(box, "");
      try {
        const options = await post("/account/passkeys/options");
        options.challenge = toBytes(options.challenge);
        options.user.id = toBytes(options.user.id);
        (options.excludeCredentials || []).forEach((c) => { c.id = toBytes(c.id); });
        const credential = await navigator.credentials.create({ publicKey: options });
        const answer = await post("/account/passkeys", {
          name: addForm.elements.name.value,
          credential: {
            id: credential.id, rawId: toText(credential.rawId), type: credential.type,
            response: {
              attestationObject: toText(credential.response.attestationObject),
              clientDataJSON: toText(credential.response.clientDataJSON),
              transports: credential.response.getTransports ? credential.response.getTransports() : [],
            },
          },
        });
        window.location.href = answer.next;
      } catch (error) {
        if (error && error.name === "NotAllowedError") return;
        if (error && error.name === "InvalidStateError") { show(box, "This device already has a passkey for Amide."); return; }
        show(box, error.message || "That passkey could not be added.");
      }
    });
  }
})();
