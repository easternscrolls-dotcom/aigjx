(function () {
  function escapeHtml(s) {
    return (s || "").replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  var INDEX = null;
  function loadIndex(cb) {
    if (INDEX) return cb(INDEX);
    fetch("/search.json")
      .then(function (r) { return r.json(); })
      .then(function (d) { INDEX = d; cb(INDEX); })
      .catch(function (e) { console.error("search index load failed", e); });
  }

  function score(item, q) {
    var t = (item.title || "").toLowerCase();
    var d = (item.description || "").toLowerCase();
    var cats = (item.categories || []).join(" ").toLowerCase();
    var tags = (item.tags || []).join(" ").toLowerCase();
    var s = 0;
    if (t.indexOf(q) >= 0) s += 10;
    if (cats.indexOf(q) >= 0) s += 5;
    if (tags.indexOf(q) >= 0) s += 3;
    if (d.indexOf(q) >= 0) s += 1;
    q.split(/\s+/).forEach(function (w) {
      if (!w) return;
      if (t.indexOf(w) >= 0) s += 2;
      if (cats.indexOf(w) >= 0) s += 1;
      if (tags.indexOf(w) >= 0) s += 1;
    });
    return s;
  }

  function render(container, items, q) {
    if (!items.length) {
      container.innerHTML = '<div class="sr-empty">No matches for “' + escapeHtml(q) + '”</div>';
      return;
    }
    container.innerHTML = items.slice(0, 8).map(function (it) {
      var desc = it.description || "";
      if (desc.length > 120) desc = desc.slice(0, 120) + "…";
      return '<a class="sr-item" href="' + escapeHtml(it.url) + '">' +
        '<span class="sr-title">' + escapeHtml(it.title) + "</span>" +
        '<span class="sr-desc">' + escapeHtml(desc) + "</span></a>";
    }).join("");
  }

  function attach(input, results) {
    input.addEventListener("input", function () {
      var q = input.value.trim().toLowerCase();
      if (!q) { results.hidden = true; results.innerHTML = ""; return; }
      loadIndex(function (idx) {
        var hits = idx.filter(function (i) { return score(i, q) > 0; })
          .sort(function (a, b) { return score(b, q) - score(a, q); });
        results.hidden = false;
        render(results, hits, q);
      });
    });
    input.addEventListener("blur", function () { setTimeout(function () { results.hidden = true; }, 150); });
  }

  document.addEventListener("DOMContentLoaded", function () {
    var boxes = document.querySelectorAll("[data-search]");
    boxes.forEach(function (box) {
      var input = box.querySelector("[data-search-input]");
      var results = box.querySelector("[data-search-results]");
      if (input && results) attach(input, results);
    });
  });
})();
