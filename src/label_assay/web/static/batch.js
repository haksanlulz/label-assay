// Batch results: poll for progress and render rows as labels finish. This is the
// only client script in the app — the single-label flow needs none.
(function () {
  "use strict";
  var jobId = document.getElementById("job").dataset.job;
  var rows = document.getElementById("rows");
  var summaryEl = document.getElementById("summary");
  var progressEl = document.getElementById("progress");
  var filter = "all";

  var LABELS = {
    pass: "Compliant", needs_review: "Needs review", fail: "Needs correction",
    not_evaluable: "Not checked", error: "Error"
  };

  function statusOf(item) {
    return item.status === "error" ? "error" : (item.verdict || "pending");
  }
  function badgeClass(status) { return status === "error" ? "not_evaluable" : status; }
  function matches(status) { return filter === "all" || status === filter; }
  function esc(s) { var d = document.createElement("div"); d.textContent = s || ""; return d.innerHTML; }

  function render(data) {
    progressEl.textContent = data.done < data.total
      ? "Checked " + data.done + " of " + data.total + "…"
      : "Done — checked " + data.total + ".";
    var s = data.summary;
    var parts = [s.pass + " compliant", s.needs_review + " need review", s.fail + " need correction"];
    if (s.error) { parts.push(s.error + " could not be read"); }
    summaryEl.textContent = parts.join(" · ");

    rows.innerHTML = "";
    data.items.forEach(function (item) {
      var status = statusOf(item);
      var label = item.status === "pending" ? "Working…" : (LABELS[status] || status);
      var tr = document.createElement("tr");
      tr.dataset.status = status;
      tr.hidden = !matches(status);
      tr.innerHTML =
        "<td>" + esc(item.filename) + "</td>" +
        '<td><span class="badge badge--' + badgeClass(status) + '">' + esc(label) + "</span></td>" +
        "<td>" + esc(item.detail) + "</td>";
      rows.appendChild(tr);
    });
  }

  function poll() {
    fetch("/batch/" + jobId + "/data").then(function (r) {
      if (!r.ok) { progressEl.textContent = "Batch not found."; return null; }
      return r.json();
    }).then(function (data) {
      if (!data) { return; }
      render(data);
      if (data.done < data.total) { setTimeout(poll, 1500); }
    }).catch(function () { setTimeout(poll, 3000); });
  }

  Array.prototype.forEach.call(document.querySelectorAll(".chip"), function (btn) {
    btn.addEventListener("click", function () {
      filter = btn.dataset.filter;
      Array.prototype.forEach.call(document.querySelectorAll(".chip"), function (c) {
        c.classList.toggle("chip--on", c === btn);
      });
      Array.prototype.forEach.call(document.querySelectorAll("#rows tr"), function (tr) {
        tr.hidden = !matches(tr.dataset.status);
      });
    });
  });

  poll();
})();
