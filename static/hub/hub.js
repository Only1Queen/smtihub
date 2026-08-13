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

    // Keyed per cell: the year grid has the same KPI twelve times over.
    var key = urlOf(input) + "#" + input.dataset.kpi;
    clearTimeout(timers[key]);
    timers[key] = setTimeout(function () { save(input); }, 450);
  });

  function urlOf(input) { return input.dataset.url || grid.dataset.saveUrl; }

  function save(input) {
    var body = new URLSearchParams({
      kpi: input.dataset.kpi,
      value: input.value,
      updated_at: input.dataset.updatedAt || "",
      csrfmiddlewaretoken: grid.dataset.csrf
    });
    fetch(urlOf(input), { method: "POST", body: body, headers: { "X-Requested-With": "fetch" } })
      .then(function (r) { return r.text().then(function (t) { return { ok: r.ok, status: r.status, html: t }; }); })
      .then(function (r) {
        var line = document.getElementById("saveline");
        // The year grid has no month totals to swap in — it just reports.
        if (!line) { say(r.ok ? "Saved" : strip(r.html), !r.ok); return; }
        if (r.status === 409 || r.status === 400) { line.innerHTML = r.html; return; }
        line.outerHTML = r.html;
      })
      .catch(function () { say("Could not reach the server — not saved", true); });
  }

  // The server's error fragment is HTML; only its text belongs in the status line.
  function strip(html) {
    var d = document.createElement("div");
    d.innerHTML = html;
    return d.textContent.trim() || "Not saved";
  }

  function say(text, isError) {
    var el = document.getElementById("saveState");
    if (!el) return;
    el.textContent = text;
    el.className = isError ? "err" : "";
  }
})();

// Appraisal year countdown in the topbar, ticking once a second.
(function () {
  var el = document.getElementById("yearClock");
  if (!el) return;
  var ends = new Date(el.dataset.ends).getTime();
  if (isNaN(ends)) { el.remove(); return; }

  function pad(n) { return n < 10 ? "0" + n : String(n); }

  function tick() {
    var left = ends - Date.now();
    if (left <= 0) { el.textContent = "year ended"; return; }
    var s = Math.floor(left / 1000);
    el.textContent = pad(Math.floor(s / 3600) % 24) + ":" +
                     pad(Math.floor(s / 60) % 60) + ":" + pad(s % 60);
    setTimeout(tick, 1000 - (Date.now() % 1000));
  }
  tick();
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
