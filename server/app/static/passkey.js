/* Passkeys (WebAuthn): Anmeldung (Vorschlag im E-Mail-Feld und Knopf), Bestätigung und Anlegen im Profil.
   Ohne JavaScript oder ohne WebAuthn bleibt alles beim klassischen Ablauf; die Knöpfe sind dann versteckt. */
(() => {
  "use strict";
  if (!window.PublicKeyCredential || !navigator.credentials) return;

  const toBuf = (s) => {
    const b = atob(s.replace(/-/g, "+").replace(/_/g, "/") + "===".slice((s.length + 3) % 4));
    return Uint8Array.from(b, (c) => c.charCodeAt(0)).buffer;
  };
  const fromBuf = (buf) => btoa(String.fromCharCode(...new Uint8Array(buf)))
    .replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");

  const requestOptions = (o) => {
    o.challenge = toBuf(o.challenge);
    (o.allowCredentials || []).forEach((c) => { c.id = toBuf(c.id); });
    return o;
  };
  const creationOptions = (o) => {
    o.challenge = toBuf(o.challenge);
    o.user.id = toBuf(o.user.id);
    (o.excludeCredentials || []).forEach((c) => { c.id = toBuf(c.id); });
    return o;
  };
  const credentialJSON = (cred) => {
    const r = cred.response;
    const out = {
      id: cred.id, rawId: fromBuf(cred.rawId), type: cred.type,
      authenticatorAttachment: cred.authenticatorAttachment || undefined,
      clientExtensionResults: cred.getClientExtensionResults ? cred.getClientExtensionResults() : {},
      response: { clientDataJSON: fromBuf(r.clientDataJSON) },
    };
    if (r.attestationObject) {
      out.response.attestationObject = fromBuf(r.attestationObject);
      if (r.getTransports) out.response.transports = r.getTransports();
    } else {
      out.response.authenticatorData = fromBuf(r.authenticatorData);
      out.response.signature = fromBuf(r.signature);
      if (r.userHandle) out.response.userHandle = fromBuf(r.userHandle);
    }
    return JSON.stringify(out);
  };

  async function post(url, csrf, data = {}) {
    const body = new URLSearchParams(data);
    body.set("csrf_token", csrf);
    const res = await fetch(url, { method: "POST", body, credentials: "same-origin", headers: { "x-csrf-token": csrf } });
    const json = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(json.error || res.statusText);
    return json;
  }

  const showError = (box, err) => {
    if (!box || (err && err.name === "AbortError")) return;
    if (err && err.name === "NotAllowedError") return;   // abgebrochen
    box.textContent = err.message || String(err);
    box.hidden = false;
  };

  // ---------- Anmeldung ----------
  const login = document.querySelector("[data-passkey-login]");
  if (login) {
    const base = login.dataset.base, csrf = login.dataset.csrf;
    const errorBox = document.querySelector("[data-passkey-error]");
    let pending = null;
    async function signIn(conditional) {
      if (pending) pending.abort();
      const ctrl = new AbortController();
      pending = ctrl;
      const { options } = await post(`${base}/passkey/options`, csrf);
      const cred = await navigator.credentials.get({
        publicKey: requestOptions(JSON.parse(options)), signal: ctrl.signal,
        ...(conditional ? { mediation: "conditional" } : {}),
      });
      const res = await post(`${base}/passkey`, csrf, { credential: credentialJSON(cred) });
      window.location.assign(res.redirect);
    }
    login.hidden = false;
    login.addEventListener("click", () => signIn(false).catch((e) => showError(errorBox, e)));
    if (PublicKeyCredential.isConditionalMediationAvailable) {
      PublicKeyCredential.isConditionalMediationAvailable().then((ok) => {
        if (ok) signIn(true).catch((e) => showError(errorBox, e));
      });
    }
  }

  // ---------- Profil: bestätigen und anlegen ----------
  const card = document.querySelector("[data-passkeys]");
  if (card) {
    const base = card.dataset.base, csrf = card.dataset.csrf;
    const errorBox = card.querySelector("[data-passkey-error]");
    const confirmBtn = card.querySelector("[data-passkey-confirm]");
    const add = card.querySelector("[data-passkey-add]");
    const unsupported = card.querySelector("[data-passkey-unsupported]");
    if (unsupported) unsupported.hidden = true;
    if (confirmBtn) {
      confirmBtn.hidden = false;
      confirmBtn.addEventListener("click", async () => {
        try {
          const { options } = await post(`${base}/confirm/passkey/options`, csrf);
          const cred = await navigator.credentials.get({ publicKey: requestOptions(JSON.parse(options)) });
          const res = await post(`${base}/confirm/passkey`, csrf, { credential: credentialJSON(cred) });
          window.location.assign(res.redirect);
        } catch (e) { showError(errorBox, e); }
      });
    }
    if (add) {
      add.hidden = false;
      add.querySelector("[data-passkey-add-button]").addEventListener("click", async () => {
        try {
          const { options } = await post(`${base}/passkeys/options`, csrf);
          const cred = await navigator.credentials.create({ publicKey: creationOptions(JSON.parse(options)) });
          const name = add.querySelector("input[name=passkey_name]").value;
          const res = await post(`${base}/passkeys`, csrf, { credential: credentialJSON(cred), name });
          window.location.assign(res.redirect);
        } catch (e) { showError(errorBox, e); }
      });
    }
  }
})();
