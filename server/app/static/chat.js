// Live-Aktualisierung des Chats per Polling. Ersetzt den Verlauf nicht, solange
// im Verlauf ein Formular bedient wird (z. B. „Anpassen“ geöffnet).
(function () {
  const box = document.getElementById("chat");
  if (!box) return;
  const scrollDown = () => { box.scrollTop = box.scrollHeight; };
  scrollDown();
  async function refresh() {
    if (box.contains(document.activeElement) || box.querySelector("details[open]")) return;
    try {
      const r = await fetch(box.dataset.src, { credentials: "same-origin" });
      if (!r.ok || r.redirected) return;
      const html = await r.text();
      if (html !== box.dataset.last) {
        const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 40;
        box.innerHTML = html;
        box.dataset.last = html;
        if (atBottom) scrollDown();
      }
    } catch (e) { /* offline – nächster Versuch */ }
  }
  setInterval(refresh, 5000);
})();
