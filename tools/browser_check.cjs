// Drive the batch results page with staged poll responses and check what the
// 2026-11-12 audit said was broken: focus survives a poll, live regions are
// not rewritten when nothing changed, a failed poll backs off, filters work.
// Usage: node batch-page.cjs <base-url> <job-id>   (server: tests/browser_server.py)
const { chromium, firefox, webkit } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const [base, job] = process.argv.slice(2);

const item = (filename, status, verdict, detail, report) => ({
  filename, status, verdict, detail, report_url: report ? "x" : null, unchecked: false,
  review_status: status === "pending" ? null : verdict,
});
const payload = (items) => {
  const done = items.filter((i) => i.status !== "pending").length;
  const count = (v) => items.filter((i) => i.verdict === v && i.status === "done").length;
  return {
    total: items.length, done, csv_rows: null, csv_unmatched: 0, items,
    summary: { pass: count("pass"), needs_review: count("needs_review"), fail: count("fail"), error: 0, not_evaluable: 0 },
  };
};
const A = item("a.png", "done", "pass", "All checks passed.", true);
const B0 = item("<b>b</b>.png", "pending", null, "", false);
const B1 = item("<b>b</b>.png", "done", "fail", "Warning text differs.", true);
const STAGES = [payload([A, B0]), payload([A, B0]), "fail", payload([A, B0]), payload([A, B1])];

async function run(engine, name) {
  const failures = [];
  const expect = (ok, what) => { if (!ok) failures.push(what); };
  const browser = await engine.launch();
  const page = await browser.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  let served = 0;
  const times = [];
  await page.route("**/batch/*/data", async (route) => {
    const stage = STAGES[Math.min(served, STAGES.length - 1)];
    served += 1;
    times.push(Date.now());
    if (stage === "fail") return route.fulfill({ status: 502, body: "bad gateway" });
    return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(stage) });
  });
  await page.goto(`${base}/batch/${job}`);
  await page.waitForSelector("#rows tr");
  // Count writes to the live regions from here on.
  await page.evaluate(() => {
    window.__writes = { progress: 0, summary: 0, filter: 0 };
    const watch = (id, key) => new MutationObserver((m) => { window.__writes[key] += m.length; })
      .observe(document.getElementById(id), { childList: true, characterData: true, subtree: true });
    watch("progress", "progress"); watch("summary", "summary"); watch("filter-status", "filter");
    window.__firstRow = document.querySelector("#rows tr");
  });
  expect(await page.locator("#rows tr").count() === 2, "two rows after the first poll");
  expect((await page.locator("#rows tr").nth(1).locator("td").first().textContent()) === "<b>b</b>.png", "filename shown as text");
  expect(await page.locator("#rows b").count() === 0, "filename markup not interpreted");
  const pending = page.locator("#rows tr").nth(1).locator(".badge");
  expect((await pending.getAttribute("class")).includes("badge--pending"), "pending badge class");
  expect((await pending.evaluate((el) => getComputedStyle(el).borderTopStyle)) === "dashed", "pending badge is styled");
  const link = page.locator("#rows tr").first().locator("a");
  await link.focus();
  // Second poll: identical payload. Nothing should be written, focus should stay.
  await page.waitForFunction(() => true, null, { timeout: 100 });
  await page.waitForTimeout(1900);
  expect(served >= 2, "a second poll happened");
  const quiet = await page.evaluate(() => window.__writes);
  expect(quiet.progress === 0 && quiet.summary === 0 && quiet.filter === 0, `unchanged poll wrote nothing (${JSON.stringify(quiet)})`);
  expect(await page.evaluate(() => document.activeElement && document.activeElement.textContent === "View findings"), "focus kept across a poll");
  expect(await page.evaluate(() => document.querySelector("#rows tr") === window.__firstRow), "row node kept across a poll");
  // Third poll fails: message, then a retry no sooner than about 3 s.
  await page.waitForFunction(() => document.getElementById("progress").textContent.includes("Connection lost"), null, { timeout: 5000 });
  const failedAt = times[2];
  await page.waitForFunction(() => document.getElementById("progress").textContent.startsWith("Checked"), null, { timeout: 8000 });
  expect(times[3] - failedAt >= 2700, `backed off after a failure (${times[3] - failedAt} ms)`);
  // Final poll: second row finishes.
  await page.waitForFunction(() => document.getElementById("progress").textContent.startsWith("Done"), null, { timeout: 6000 });
  expect((await page.locator("#rows tr").nth(1).locator(".badge").textContent()) === "Needs correction", "row updated in place");
  expect(await page.evaluate(() => document.activeElement && document.activeElement.textContent === "View findings"), "focus kept through completion");
  const servedAtDone = served;
  await page.waitForTimeout(2200);
  expect(served === servedAtDone, "polling stops when the batch is done");
  // Filters.
  await page.locator('.chip[data-filter="fail"]').click();
  expect(await page.locator("#rows tr:not([hidden])").count() === 1, "filter hides the compliant row");
  expect((await page.locator("#filter-status").textContent()) === "Needs correction: showing 1 of 2 labels.", "filter status text");
  expect(errors.length === 0, `no page errors (${errors.join("; ")})`);
  // Result page: the script-only buttons show with scripting on and are absent without it.
  const BUTTONS = "[data-print],[data-download-html],[data-save-report]";
  const withJs = await browser.newPage();
  await withJs.goto(`${base}/batch/${job}/items/0`);
  await withJs.waitForLoadState("load");
  const present = await withJs.locator(BUTTONS).count();
  expect(present >= 2, `result page carries report buttons (${present})`);
  expect(await withJs.locator(BUTTONS).evaluateAll((els) => els.every((el) => el.offsetParent !== null)), "buttons visible with scripting on");
  const plain = await browser.newContext({ javaScriptEnabled: false });
  const withoutJs = await plain.newPage();
  await withoutJs.goto(`${base}/batch/${job}/items/0`);
  expect(await withoutJs.locator(BUTTONS).evaluateAll((els) => els.length > 0 && els.every((el) => el.hasAttribute("hidden"))), "buttons hidden with scripting off");
  expect(await withoutJs.locator(".findings .finding").count() > 0, "findings still render with scripting off");
  await browser.close();
  console.log(`${name}: ${failures.length ? "FAILED" : "ok"}${failures.map((f) => "\n  - " + f).join("")}`);
  return failures.length;
}

(async () => {
  let bad = 0;
  for (const [engine, name] of [[chromium, "chromium"], [firefox, "firefox"], [webkit, "webkit"]]) bad += await run(engine, name);
  process.exit(bad ? 1 : 0);
})();
