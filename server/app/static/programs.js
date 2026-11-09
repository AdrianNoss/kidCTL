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
    const boxes = [...group.querySelectorAll('input[name="app"]')];
    const n = boxes.filter((b) => b.checked).length;
    const pill = group.querySelector(".app-count");
    pill.textContent = root.dataset.countText.replace("{n}", n).replace("{total}", boxes.length);
    pill.classList.toggle("ok", n > 0);
  }

  const onlyLabel = root.querySelector(".app-only-allowed");
  const onlyAllowed = onlyLabel.querySelector("input");
  const note = root.closest("form").querySelector(".save-note");
  const markDirty = () => { note.hidden = false; };

  function applyFilter() {
    const q = search.value.trim().toLowerCase();
    const only = onlyAllowed.checked;
    let any = false;
    for (const g of groups) {
      let hits = 0;
      for (const item of g.querySelectorAll(".app-item")) {
        const show = (!q || item.dataset.name.includes(q))
          && (!only || item.querySelector('input[name="app"]').checked);
        item.hidden = !show;
        if (show) hits++;
      }
      g.hidden = hits === 0;
      g.open = q || only ? hits > 0 : initiallyOpen.get(g);
      any ||= hits > 0;
    }
    nomatch.hidden = any;
  }

  search.hidden = false;
  onlyLabel.hidden = false;
  search.addEventListener("input", applyFilter);
  onlyAllowed.addEventListener("change", applyFilter);

  for (const g of groups) {
    g.querySelector(".app-bulk").hidden = false;
    g.addEventListener("change", () => { updateCount(g); markDirty(); });
    for (const btn of g.querySelectorAll("[data-bulk]")) {
      btn.addEventListener("click", (ev) => {
        ev.preventDefault(); // Klick in <summary> soll die Gruppe nicht auf-/zuklappen
        for (const item of g.querySelectorAll(".app-item")) {
          if (!item.hidden) item.querySelector("input").checked = btn.dataset.bulk === "all";
        }
        updateCount(g);
        markDirty();
      });
    }
  }
})();
