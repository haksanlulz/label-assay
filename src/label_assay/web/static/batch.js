// Batch results: poll for progress and render rows as labels finish. The other
// client scripts are the upload forms' submit guard (submit-guard.js) and the
// report buttons (reports.js); checking a single label works with scripting
// disabled.
(function () {
  "use strict";
  var jobId = document.getElementById("job").dataset.job;
  var rows = document.getElementById("rows");
  var summaryEl = document.getElementById("summary");
  var progressEl = document.getElementById("progress");
  var filterStatusEl = document.getElementById("filter-status");
  var filter = "all";
  var built = [];  // per row index: the <tr> and the values it currently shows

  // A live region announces whatever is written to it, changed or not on some
  // screen readers, so nothing is written unless it differs.
  function setText(el, text) {
    if (el.textContent !== text) { el.textContent = text; }
  }
  function announceFilter() {
    var shown = rows.querySelectorAll("tr:not([hidden])").length;
    var selected = document.querySelector('.chip[aria-pressed="true"]');
    setText(filterStatusEl, selected.textContent + ": showing " + shown + " of " + built.length + " labels.");
  }

  var LABELS = {
    pass: "Compliant", needs_review: "Needs review", fail: "Needs correction",
    not_evaluable: "Not checked", error: "Could not be read"
  };

  function statusOf(item) {
    return item.review_status || (item.status === "error" ? "error" : (item.verdict || "pending"));
  }
  // The result value becomes part of a class name, so it must be one of a fixed
  // set — never an arbitrary string. Every real status already maps into this
  // set; clamping keeps that true even if the server contract ever drifts.
  var BADGE_CLASSES = ["pass", "needs_review", "fail", "not_evaluable", "pending"];
  function badgeClass(status) {
    var cls = status === "error" ? "not_evaluable" : status;
    return BADGE_CLASSES.indexOf(cls) === -1 ? "not_evaluable" : cls;
  }
  function matches(status, unchecked) {
    return filter === "all" || status === filter || (filter === "unchecked" && unchecked) ||
      (filter === "queue" && ["fail", "needs_review", "not_evaluable", "error"].indexOf(status) !== -1);
  }

  // A row is built once and then only the cells whose values changed are
  // touched. Rebuilding the table on every poll threw away keyboard focus and a
  // screen reader's place in it every second and a half while a batch ran.
  // Everything is set as text or through the DOM; no markup is assembled from
  // server values.
  function buildRow(index) {
    var tr = document.createElement("tr");
    var file = tr.insertCell();
    var result = tr.insertCell();
    var badge = document.createElement("span");
    result.appendChild(badge);
    var detailCell = tr.insertCell();
    var detail = document.createElement("span");
    detailCell.appendChild(detail);
    rows.appendChild(tr);
    return { tr: tr, file: file, badge: badge, detailCell: detailCell, detail: detail, link: null, shown: "" };
  }

  function updateRow(row, item, index) {
    var status = statusOf(item);
    var label = item.status === "pending" ? "Working…" : (LABELS[status] || status);
    if (status === "not_evaluable" && item.verdict === "pass") { label = "Checks incomplete"; }
    var unchecked = item.unchecked ? "true" : "false";
    var shown = [item.filename, status, label, item.detail || "", unchecked, item.report_url ? "1" : "0"].join("\u0001");
    if (shown !== row.shown) {
      row.shown = shown;
      setText(row.file, item.filename || "");
      row.badge.className = "badge badge--" + badgeClass(status);
      setText(row.badge, label);
      setText(row.detail, item.detail || "");
      row.tr.dataset.status = status;
      row.tr.dataset.unchecked = unchecked;
      if (item.report_url && !row.link) {
        row.link = document.createElement("a");
        // Build the local route from the job and row, never an uploaded filename.
        row.link.href = "/batch/" + encodeURIComponent(jobId) + "/items/" + index;
        row.link.textContent = "View findings";
        row.link.setAttribute("aria-label", "View findings for " + item.filename);
        row.detailCell.appendChild(document.createElement("br"));
        row.detailCell.appendChild(row.link);
      }
    }
    var hide = !matches(status, item.unchecked);
    if (row.tr.hidden !== hide) { row.tr.hidden = hide; }
  }

  function render(data) {
    setText(progressEl, data.done < data.total
      ? "Checked " + data.done + " of " + data.total + "…"
      : "Done — checked " + data.total + ".");
    var s = data.review_summary || data.summary;
    var parts = [s.pass + " compliant", s.needs_review + " need review", s.fail + " need correction"];
    if (s.error) { parts.push(s.error + " could not be read"); }
    if (s.not_evaluable) { parts.push(s.not_evaluable + " have incomplete checks"); }
    if (data.csv_rows !== null && data.csv_rows !== undefined && data.csv_unmatched > 0) {
      parts.push(data.csv_unmatched + " had no matching application row");
    }
    setText(summaryEl, parts.join(" · "));

    data.items.forEach(function (item, index) {
      if (!built[index]) { built[index] = buildRow(index); }
      updateRow(built[index], item, index);
    });
    announceFilter();
  }

  // Polling. A failed request backs off (3 s doubling to a minute) so a host
  // that is down is not hammered by every open tab, and a hidden tab waits
  // until it is looked at again.
  var POLL_MS = 1500, RETRY_MS = 3000, RETRY_MAX_MS = 60000;
  var retryMs = RETRY_MS;
  var timer = null;
  var finished = false;

  function schedule(ms) {
    clearTimeout(timer);
    timer = setTimeout(poll, ms);
  }

  function poll() {
    if (finished) { return; }
    if (document.hidden) { return; }  // visibilitychange resumes
    fetch("/batch/" + jobId + "/data").then(function (r) {
      // Only a real 404 means the job is gone; a transient 500/502 from a proxy
      // must not end polling with a false "not found".
      if (r.status === 404) { finished = true; setText(progressEl, "Batch not found."); return null; }
      if (!r.ok) { throw new Error("HTTP " + r.status); }
      return r.json();
    }).then(function (data) {
      if (!data) { return; }
      retryMs = RETRY_MS;
      render(data);
      if (data.done < data.total) { schedule(POLL_MS); } else { finished = true; }
    }).catch(function () {
      setText(progressEl, "Connection lost. Trying again in " + Math.round(retryMs / 1000) + " seconds…");
      schedule(retryMs);
      retryMs = Math.min(retryMs * 2, RETRY_MAX_MS);
    });
  }

  document.addEventListener("visibilitychange", function () {
    if (!document.hidden && !finished) { schedule(0); }
  });

  Array.prototype.forEach.call(document.querySelectorAll(".chip"), function (btn) {
    btn.addEventListener("click", function () {
      filter = btn.dataset.filter;
      Array.prototype.forEach.call(document.querySelectorAll(".chip"), function (c) {
        c.classList.toggle("chip--on", c === btn);
        c.setAttribute("aria-pressed", c === btn ? "true" : "false");
      });
      built.forEach(function (row) {
        row.tr.hidden = !matches(row.tr.dataset.status, row.tr.dataset.unchecked === "true");
      });
      announceFilter();
    });
  });

  poll();
})();
