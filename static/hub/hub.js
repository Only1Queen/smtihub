// Theme: "system" clears data-theme so prefers-color-scheme applies; light/dark
// stamp the root, which overrides the media query in both directions.
(function () {
  var root = document.documentElement;
  var stored = document.cookie.replace(/(?:(?:^|.*;\s*)theme\s*=\s*([^;]*).*$)|^.*$/, "$1");
  if (stored === "light" || stored === "dark") root.dataset.theme = stored;
  else delete root.dataset.theme;

  document.addEventListener("click", function (e) {
    var b = e.target.closest("[data-theme-set]");
    if (!b) return;
    var v = b.dataset.themeSet;
    document.cookie = "theme=" + v + ";path=/;max-age=31536000;samesite=Lax";
    if (v === "system") delete root.dataset.theme; else root.dataset.theme = v;
    document.querySelectorAll("[data-theme-set]").forEach(function (x) {
      x.setAttribute("aria-pressed", x.dataset.themeSet === v);
    });
  });
})();

// Score grid: autosave one cell at a time, carrying the updated_at it loaded
// with so a stale write 409s instead of overwriting the other tab.
(function () {
  var grid = document.getElementById("scoreGrid");
  if (!grid) return;
  var timers = {};

  grid.addEventListener("input", function (e) {
    var input = e.target;
    if (!input.matches("input[data-kpi]")) return;
    var max = Number(input.dataset.max);
    var bad = input.value !== "" && (Number(input.value) > max || Number(input.value) < 0);
    input.classList.toggle("over", bad);
    say(bad ? "Score exceeds the KPI maximum — not saved" : "Saving…", bad);
    if (bad) return;

    clearTimeout(timers[input.dataset.kpi]);
    timers[input.dataset.kpi] = setTimeout(function () { save(input); }, 450);
  });

  function save(input) {
    var body = new URLSearchParams({
      kpi: input.dataset.kpi,
      value: input.value,
      updated_at: input.dataset.updatedAt || "",
      csrfmiddlewaretoken: grid.dataset.csrf
    });
    fetch(grid.dataset.saveUrl, { method: "POST", body: body, headers: { "X-Requested-With": "fetch" } })
      .then(function (r) { return r.text().then(function (t) { return { ok: r.ok, status: r.status, html: t }; }); })
      .then(function (r) {
        var line = document.getElementById("saveline");
        if (r.status === 409 || r.status === 400) { line.innerHTML = r.html; return; }
        line.outerHTML = r.html;
      })
      .catch(function () { say("Could not reach the server — not saved", true); });
  }

  function say(text, isError) {
    var el = document.getElementById("saveState");
    if (!el) return;
    el.textContent = text;
    el.className = isError ? "err" : "";
  }
})();

// Goal detail rows on the analyst view.
document.addEventListener("click", function (e) {
  var row = e.target.closest(".goal-toggle");
  if (!row) return;
  var detail = document.getElementById(row.dataset.detail);
  if (!detail) return;
  var open = row.getAttribute("aria-expanded") === "true";
  row.setAttribute("aria-expanded", !open);
  detail.classList.toggle("hidden", open);
  var chev = row.querySelector(".chev");
  if (chev) chev.classList.toggle("open", !open);
});

// Task form: weight only applies to a KPI-linked task.
(function () {
  var kpi = document.getElementById("id_kpi");
  var wrap = document.getElementById("row-weight");
  if (!kpi || !wrap) return;
  function sync() { wrap.classList.toggle("hidden", !kpi.value); }
  kpi.addEventListener("change", sync);
  sync();
})();
