// Suche (Pagefind, je Sprache eigener Index). Index wird erst beim Tippen geladen; keine externen Requests.
// Strg+K oder / öffnet den Dialog, Esc schließt ihn. Ohne JavaScript führt der Suchknopf auf die Suchseite.
(() => {
  let pagefind = null;
  async function load(box) {
    if (!pagefind) {
      pagefind = await import(box.dataset.bundle + "pagefind.js");
      await pagefind.options({ baseUrl: box.dataset.base, excerptLength: 24 });
      pagefind.init();
    }
    return pagefind;
  }

  function wire(box) {
    const input = box.querySelector("input");
    const status = box.querySelector(".search-status");
    const list = box.querySelector(".search-results");
    const filters = { docs: box.dataset.filterDocs, news: box.dataset.filterNews };
    let filter = "", seq = 0, timer;

    async function run() {
      const q = input.value.trim(), my = ++seq;
      if (!q) { list.innerHTML = ""; status.textContent = ""; return; }
      try {
        const pf = await load(box);
        const res = await pf.search(q, filter ? { filters: { type: filters[filter] } } : {});
        const items = await Promise.all(res.results.slice(0, 12).map((r) => r.data()));
        if (my !== seq) return;
        status.textContent = items.length ? box.dataset.tCount.replace("{n}", res.results.length) : box.dataset.tEmpty;
        list.innerHTML = "";
        for (const it of items) {
          const li = document.createElement("li");
          const a = document.createElement("a");
          a.href = it.url;
          const t = document.createElement("span");
          t.className = "search-title";
          t.textContent = it.meta.title || it.url;
          const tag = it.filters && it.filters.type && it.filters.type[0];
          if (tag) { const c = document.createElement("span"); c.className = "chip"; c.textContent = tag; t.append(" ", c); }
          const ex = document.createElement("span");
          ex.className = "search-excerpt";
          ex.innerHTML = it.excerpt;            // Pagefind maskiert den Text, nur <mark> ist HTML
          a.append(t, ex);
          li.append(a);
          list.append(li);
        }
      } catch (e) {
        status.textContent = box.dataset.tError;
      }
    }
    input.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(run, 120); });
    input.addEventListener("focus", () => load(box).catch(() => {}), { once: true });
    box.querySelectorAll("[data-filter]").forEach((b) => b.addEventListener("click", () => {
      filter = b.dataset.filter;
      box.querySelectorAll("[data-filter]").forEach((x) => x.classList.toggle("active", x === b));
      run();
    }));
    input.addEventListener("keydown", (e) => {
      if (e.key === "ArrowDown") { const f = list.querySelector("a"); if (f) { e.preventDefault(); f.focus(); } }
    });
    list.addEventListener("keydown", (e) => {
      const links = [...list.querySelectorAll("a")], i = links.indexOf(document.activeElement);
      if (e.key === "ArrowDown" && i < links.length - 1) { e.preventDefault(); links[i + 1].focus(); }
      if (e.key === "ArrowUp") { e.preventDefault(); (i > 0 ? links[i - 1] : input).focus(); }
    });
    const q = new URLSearchParams(location.search).get("q");
    if (q && box.closest(".search-page")) { input.value = q; run(); }
    return input;
  }

  document.querySelectorAll(".search-page [data-search]").forEach((b) => { b.closest(".search-page").classList.add("ready"); wire(b); });
  const dlg = document.querySelector("dialog.search-dialog");
  const open = document.querySelector("[data-search-open]");
  if (!dlg || !open) return;
  const input = wire(dlg.querySelector("[data-search]"));
  function show() { if (!dlg.open) { dlg.showModal(); input.focus(); input.select(); } }
  open.addEventListener("click", (e) => { e.preventDefault(); show(); });
  dlg.addEventListener("click", (e) => { if (e.target === dlg) dlg.close(); });
  document.addEventListener("keydown", (e) => {
    const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName) || document.activeElement.isContentEditable;
    if ((e.key === "k" && (e.ctrlKey || e.metaKey)) || (e.key === "/" && !typing)) { e.preventDefault(); show(); }
  });
})();
