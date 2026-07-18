// Double-submit guard for the upload forms. A check is a paid model call and
// takes a few seconds; an impatient re-click (or a repeated Enter) during the
// wait fires a duplicate POST. Progressive enhancement only: with scripting
// off the forms submit exactly as before, the button stays enabled.
//
// Nothing here writes an element's styles — the CSP has no 'unsafe-inline' —
// the guard toggles one class on the form and app.css owns the progress
// reveal and the fill animation.
(function () {
  "use strict";
  Array.prototype.forEach.call(document.querySelectorAll("form"), function (form) {
    var button = form.querySelector("button[type=submit]");
    if (!button) { return; }
    var idleLabel = button.textContent;
    var busy = false;

    // Listening on submit, not click: submit fires only after the browser's
    // own validation passes, so a form the browser refused to send is never
    // left with a dead disabled button.
    form.addEventListener("submit", function (event) {
      if (busy) { event.preventDefault(); return; }
      busy = true;
      button.disabled = true;
      if (button.dataset.busyLabel) {
        // The wording rides on the button so each form can name its slow
        // part; the new text is still the button's accessible name.
        button.textContent = button.dataset.busyLabel;
      }
      form.classList.add("is-busy");
    });

    // Back/forward cache: the browser can restore this page exactly as it was
    // left — button disabled, bar shown. pageshow fires on that restore (and
    // on a normal load, where this is a no-op), so Back never lands on a form
    // that cannot be submitted.
    window.addEventListener("pageshow", function () {
      busy = false;
      button.disabled = false;
      button.textContent = idleLabel;
      form.classList.remove("is-busy");
    });
  });
})();
