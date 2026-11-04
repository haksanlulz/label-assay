(function () {
  "use strict";
  document.querySelectorAll("[data-print]").forEach(function (button) {
    button.addEventListener("click", function () { window.print(); });
  });
  document.querySelectorAll("[data-download-html]").forEach(function (button) {
    button.addEventListener("click", async function () {
      try {
        var response = await fetch("/static/app.css");
        if (!response.ok) { throw new Error("Stylesheet unavailable"); }
        var css = await response.text();
        var copy = document.documentElement.cloneNode(true);
        copy.querySelectorAll("script,button,.no-print,.back,.label-preview,link[rel=stylesheet]").forEach(function (node) { node.remove(); });
        copy.querySelectorAll("a[href]").forEach(function (link) { if (!/^https?:/i.test(link.getAttribute("href"))) { link.removeAttribute("href"); } });
        var style = document.createElement("style"); style.textContent = css; copy.querySelector("head").append(style);
        var policy = document.createElement("meta"); policy.httpEquiv = "Content-Security-Policy"; policy.content = "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'"; copy.querySelector("head").prepend(policy);
        var url = URL.createObjectURL(new Blob(["<!doctype html>" + copy.outerHTML], {type: "text/html"}));
        var link = document.createElement("a"); link.href = url; link.download = "label-assay-report.html"; link.click();
        setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
      } catch (_) { button.textContent = "Could not prepare HTML — try again"; }
    });
  });
  document.querySelectorAll("[data-save-report]").forEach(function (button) {
    button.addEventListener("click", function () {
      var url = URL.createObjectURL(new Blob([button.dataset.saveReport], {type: "application/json"}));
      var link = document.createElement("a"); link.href = url; link.download = "label-assay-report.json";
      link.click(); setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
    });
  });
  document.querySelectorAll("[data-report-select]").forEach(function (input) {
    input.addEventListener("change", async function () {
      var select = document.getElementById(input.dataset.reportSelect);
      var error = document.getElementById("report-error");
      var selectedFile = input.files[0];
      input.setAttribute("aria-describedby", "report-error");
      select.replaceChildren();
      try {
        var file = input.files[0];
        if (!file || file.size > 5 * 1024 * 1024) { throw new Error("Choose a JSON report no larger than 5 MB."); }
        var data = JSON.parse(await file.text());
        if (input.files[0] !== selectedFile) { return; }
        if (data.schema_version !== 1 || !Array.isArray(data.items)) { throw new Error("Choose a LabelAssay full-report JSON export."); }
        data.items.forEach(function (item, index) {
          if (item.status !== "done" || !item.report) { return; }
          var option = document.createElement("option"); option.value = index;
          option.textContent = item.filename + " (row " + index + ")"; select.append(option);
        });
        if (!select.options.length) { throw new Error("This export has no completed label reports."); }
        input.setCustomValidity(""); input.removeAttribute("aria-invalid"); select.focus();
      } catch (err) {
        if (input.files[0] !== selectedFile) { return; }
        input.setCustomValidity(err.message); input.setAttribute("aria-invalid", "true");
      }
      error.textContent = Array.from(document.querySelectorAll("[data-report-select]")).filter(function (field) {
        return field.validity.customError;
      }).map(function (field) {
        return field.closest("fieldset").querySelector("legend").textContent + ": " + field.validationMessage;
      }).join(" ");
      if (input.validity.customError) { error.focus(); }
    });
  });
})();
