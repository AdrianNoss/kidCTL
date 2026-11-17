// Wiki: Kopier-Button an Codeblöcken. Ohne JavaScript bleiben die Blöcke normal markierbar.
(() => {
  const label = document.documentElement.lang.startsWith("de") ? ["Kopieren", "Kopiert"] : ["Copy", "Copied"];
  document.querySelectorAll(".doc-body pre").forEach((pre) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "copy-btn";
    btn.textContent = label[0];
    btn.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText((pre.querySelector("code") || pre).textContent.replace(/\n$/, ""));
        btn.textContent = label[1];
        setTimeout(() => (btn.textContent = label[0]), 1500);
      } catch { /* kein Zugriff auf die Zwischenablage: Text bleibt markierbar */ }
    });
    pre.classList.add("has-copy");
    pre.appendChild(btn);
  });
})();
