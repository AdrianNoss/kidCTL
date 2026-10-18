// Programmliste: Suche und „Alle/Keine“ je Gruppe (ohne JavaScript bleibt die Liste voll bedienbar).
// „Programm hinzufügen“ öffnet einen Dialog (ohne JavaScript: Seite mit geöffnetem Dialog).
(() => {
  const dialog = document.getElementById("add-program");
  if (!dialog || !dialog.showModal) return;
  if (dialog.open) { dialog.close(); dialog.showModal(); }
  for (const a of document.querySelectorAll('[data-dialog="add-program"]')) {
    a.addEventListener("click", (ev) => { ev.preventDefault(); dialog.showModal(); dialog.querySelector("input[name=path]").focus(); });
  }
  dialog.querySelector("[data-close]").addEventListener("click", (ev) => { ev.preventDefault(); dialog.close(); });
})();

(() => {
  const root = document.querySelector(".app-catalog");
  if (!root) return;
  const search = root.querySelector(".app-search");
  const nomatch = root.querySelector(".app-nomatch");
  const groups = [...root.querySelectorAll(".app-group")];
  const initiallyOpen = new Map(groups.map((g) => [g, g.open]));

  function updateCount(group) {
    const boxes = [...group.querySelectorAll('input[type="checkbox"]')];
    const n = boxes.filter((b) => b.checked).length;
    const pill = group.querySelector(".app-count");
    pill.textContent = `${n} von ${boxes.length} erlaubt`;
    pill.classList.toggle("ok", n > 0);
  }

  search.hidden = false;
  search.addEventListener("input", () => {
    const q = search.value.trim().toLowerCase();
    let any = false;
    for (const g of groups) {
      let hits = 0;
      for (const item of g.querySelectorAll(".app-item")) {
        const show = !q || item.dataset.name.includes(q);
        item.hidden = !show;
        if (show) hits++;
      }
      g.hidden = hits === 0;
      g.open = q ? hits > 0 : initiallyOpen.get(g);
      any ||= hits > 0;
    }
    nomatch.hidden = any;
  });

  for (const g of groups) {
    g.querySelector(".app-bulk").hidden = false;
    g.addEventListener("change", () => updateCount(g));
    for (const btn of g.querySelectorAll("[data-bulk]")) {
      btn.addEventListener("click", (ev) => {
        ev.preventDefault(); // Klick in <summary> soll die Gruppe nicht auf-/zuklappen
        for (const item of g.querySelectorAll(".app-item")) {
          if (!item.hidden) item.querySelector("input").checked = btn.dataset.bulk === "all";
        }
        updateCount(g);
      });
    }
  }
})();
