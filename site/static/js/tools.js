(function () {
  document.addEventListener("DOMContentLoaded", function () {
    var wrap = document.getElementById("tool-grid");
    if (!wrap) return;
    var btns = document.querySelectorAll("#cat-filters .filter-btn");
    btns.forEach(function (b) {
      b.addEventListener("click", function () {
        var cat = b.getAttribute("data-cat");
        btns.forEach(function (x) { x.classList.remove("active"); });
        b.classList.add("active");
        var cells = wrap.querySelectorAll(".tool-cell");
        cells.forEach(function (c) {
          var cats = (c.getAttribute("data-cats") || "").split(" ").filter(Boolean);
          if (cat === "all" || cats.indexOf(cat) >= 0) c.style.display = "";
          else c.style.display = "none";
        });
      });
    });
  });
})();
