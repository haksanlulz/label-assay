// Batch results: poll for progress and render rows as labels finish. The one
// other client script is the upload forms' submit guard (submit-guard.js);
// the single-label flow works with scripting disabled.
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
    return item.review_status || (item.status === "error" ? "error" : (item.verdict || "pending"));
  }
  // The result value is written into a class attribute via innerHTML, so it must
  // be one of a fixed set — never an arbitrary string. Every real status already
  // maps into this set; clamping guarantees no value can break out of the
  // attribute even if the server contract ever drifts.
  var BADGE_CLASSES = ["pass", "needs_review", "fail", "not_evaluable", "pending"];
  function badgeClass(status) {
    var cls = status === "error" ? "not_evaluable" : status;
    return BADGE_CLASSES.indexOf(cls) === -1 ? "not_evaluable" : cls;
  }
  function matches(status, unchecked) {
    return filter === "all" || status === filter || (filter === "unchecked" && unchecked) ||
      (filter === "queue" && ["fail", "needs_review", "not_evaluable", "error"].indexOf(status) !== -1);
  }
  function esc(s) { var d = document.createElement("div"); d.textContent = s || ""; return d.innerHTML; }

  function render(data) {
    progressEl.textContent = data.done < data.total
      ? "Checked " + data.done + " of " + data.total + "…"
      : "Done — checked " + data.total + ".";
    var s = data.review_summary || data.summary;
    var parts = [s.pass + " compliant", s.needs_review + " need review", s.fail + " need correction"];
    if (s.error) { parts.push(s.error + " could not be read"); }
    if (s.not_evaluable) { parts.push(s.not_evaluable + " have incomplete checks"); }
    if (data.csv_rows !== null && data.csv_rows !== undefined && data.csv_unmatched > 0) {
      parts.push(data.csv_unmatched + " had no matching application row");
    }
    summaryEl.textContent = parts.join(" · ");

    rows.innerHTML = "";
    data.items.forEach(function (item, index) {
      var status = statusOf(item);
      var label = item.status === "pending" ? "Working…" : (LABELS[status] || status);
      var tr = document.createElement("tr");
      tr.dataset.status = status;
      tr.dataset.unchecked = item.unchecked ? "true" : "false";
      tr.hidden = !matches(status, item.unchecked);
      if (status === "not_evaluable" && item.verdict === "pass") { label = "Checks incomplete"; }
      tr.innerHTML =
        "<td>" + esc(item.filename) + "</td>" +
        '<td><span class="badge badge--' + badgeClass(status) + '">' + esc(label) + "</span></td>" +
        "<td>" + esc(item.detail) + "</td>";
      if (item.report_url) {
        var link = document.createElement("a");
        // Build the local route from the job and row, never an uploaded filename.
        link.href = "/batch/" + encodeURIComponent(jobId) + "/items/" + index;
        link.textContent = "View findings";
        link.setAttribute("aria-label", "View findings for " + item.filename);
        tr.cells[2].appendChild(document.createElement("br"));
        tr.cells[2].appendChild(link);
      }
      rows.appendChild(tr);
    });
  }

  function poll() {
    fetch("/batch/" + jobId + "/data").then(function (r) {
      // Only a real 404 means the job is gone; a transient 500/502 from a proxy
      // must not end polling with a false "not found".
      if (r.status === 404) { progressEl.textContent = "Batch not found."; return null; }
      if (!r.ok) { throw new Error("HTTP " + r.status); }
      return r.json();
    }).then(function (data) {
      if (!data) { return; }
      render(data);
      if (data.done < data.total) { setTimeout(poll, 1500); }
    }).catch(function () {
      progressEl.textContent = "Connection lost — retrying…";
      setTimeout(poll, 3000);
    });
  }

  Array.prototype.forEach.call(document.querySelectorAll(".chip"), function (btn) {
    btn.addEventListener("click", function () {
      filter = btn.dataset.filter;
      Array.prototype.forEach.call(document.querySelectorAll(".chip"), function (c) {
        c.classList.toggle("chip--on", c === btn);
        c.setAttribute("aria-pressed", c === btn ? "true" : "false");
      });
      Array.prototype.forEach.call(document.querySelectorAll("#rows tr"), function (tr) {
        tr.hidden = !matches(tr.dataset.status, tr.dataset.unchecked === "true");
      });
    });
  });

  poll();
})();
