/* Burger-Menü schließen: Link, Esc, Klick daneben. Ohne JS bleibt das <details> normal bedienbar. */
(() => {
  const m = document.querySelector("details.menu");
  if (!m) return;
  const close = () => { m.open = false; };
  m.addEventListener("click", e => { if (e.target.closest("a")) close(); });
  document.addEventListener("click", e => { if (m.open && !m.contains(e.target)) close(); });
  document.addEventListener("keydown", e => { if (e.key === "Escape" && m.open) { close(); m.querySelector("summary").focus(); } });
})();
