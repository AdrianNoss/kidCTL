// Lightbox für Screenshots: Ohne JS öffnet der Link einfach das Bild. Kein Tracking, keine Abhängigkeiten.
(() => {
  const dlg = document.createElement("dialog");
  dlg.className = "lightbox";
  dlg.innerHTML = '<button type="button" aria-label="Close">×</button><img alt="">';
  const img = dlg.querySelector("img");
  dlg.querySelector("button").addEventListener("click", () => dlg.close());
  dlg.addEventListener("click", (e) => { if (e.target === dlg) dlg.close(); });
  document.body.appendChild(dlg);
  document.addEventListener("click", (e) => {
    const a = e.target.closest("a[data-lightbox]");
    if (!a || e.ctrlKey || e.metaKey || e.shiftKey) return;
    e.preventDefault();
    const shown = a.querySelector("img");
    const dark = matchMedia("(prefers-color-scheme: dark)").matches;
    img.src = dark && a.dataset.dark ? a.dataset.dark : a.href;
    img.alt = shown ? shown.alt : "";
    dlg.showModal();
  });
})();
