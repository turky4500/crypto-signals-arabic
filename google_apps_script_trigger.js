/**
 * Google Apps Script watchdog/dispatcher for:
 *   turky4500/crypto-signals-arabic
 *
 * It does NOT scan Binance or send trading signals itself. Every five minutes it:
 *   1) checks GitHub Actions for an active monitor.yml run;
 *   2) skips if one is queued/running;
 *   3) otherwise sends workflow_dispatch to main.
 *
 * Optional: set ALERT_EMAIL in Script Properties to receive one email when the
 * workflow/API fails or a run stays active too long, plus a recovery email.
 */

const GAS_WATCHDOG = Object.freeze({
  owner: "turky4500",
  repo: "crypto-signals-arabic",
  workflow: "monitor.yml",
  ref: "main",
  runsPerPage: 20,
  minDispatchIntervalSeconds: 90,
  queuedAlertAfterMinutes: 10,
  runningAlertAfterMinutes: 25,
  defaultAlertCooldownMinutes: 60,
  githubApiVersion: "2022-11-28",
});

/** Main handler: configure the installable time-driven trigger to call this. */
function triggerCryptoSignalWorkflow() {
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(20000)) {
    console.log("Skip: another Apps Script execution holds the lock.");
    return;
  }

  try {
    const cfg = getConfig_();
    const runs = fetchWorkflowRuns_(cfg);

    // Notify about the most recent completed run, if it failed.
    inspectLatestCompletedRun_(runs);

    // Never start a second monitor run while one is queued/running.
    const activeRuns = runs.filter(isActiveRun_);
    if (activeRuns.length > 0) {
      const activeRun = activeRuns[0];
      notifyIfRunIsStuck_(activeRun);
      console.log(
        "Skip dispatch: active run #" + activeRun.run_number +
        " (" + activeRun.status + ")."
      );
      return;
    }

    // Small extra guard against duplicate dispatches if GitHub has not yet
    // reflected a just-created run in the runs API.
    const props = PropertiesService.getScriptProperties();
    const now = Date.now();
    const lastDispatchAt = Number(props.getProperty("LAST_DISPATCH_AT_MS") || 0);
    const minimumGapMs = GAS_WATCHDOG.minDispatchIntervalSeconds * 1000;
    if (lastDispatchAt && (now - lastDispatchAt) < minimumGapMs) {
      console.log("Skip dispatch: minimum dispatch interval has not elapsed.");
      return;
    }

    dispatchWorkflow_(cfg);
    props.setProperty("LAST_DISPATCH_AT_MS", String(now));
    console.log("Workflow dispatched successfully at " + new Date().toISOString());
  } catch (err) {
    const message = errorText_(err);
    console.error("Watchdog error: " + message);
    sendAlertOnce_(
      "github-dispatcher-error",
      "Crypto monitor: تعذّر تشغيل المراقبة",
      "تعذّر على Google Apps Script فحص GitHub أو إطلاق workflow.\n\n" + message
    );
    // Keep this execution marked failed in Apps Script's Executions page.
    throw err;
  } finally {
    lock.releaseLock();
  }
}

/** Safe manual test: checks Script Properties and GitHub API; does not dispatch. */
function testGitHubConnection() {
  const cfg = getConfig_();
  const runs = fetchWorkflowRuns_(cfg);
  console.log("GitHub API OK. Recent workflow runs returned: " + runs.length);
  runs.slice(0, 5).forEach((run) => {
    console.log(
      "#" + run.run_number + " " + run.status + " " +
      (run.conclusion || "") + " " + (run.html_url || "")
    );
  });
}

/** Run once from the editor to authorize MailApp (only if ALERT_EMAIL is set). */
function authorizeEmailNotifications() {
  const email = getAlertEmail_();
  if (!email) {
    throw new Error("Set ALERT_EMAIL in Script Properties first, or skip email alerts.");
  }
  console.log("Mail authorization OK. Remaining daily recipient quota: " +
              MailApp.getRemainingDailyQuota());
}

/** Creates exactly one five-minute trigger for the main handler. */
function createFiveMinuteTrigger() {
  deleteWorkflowTriggers();
  ScriptApp.newTrigger("triggerCryptoSignalWorkflow")
    .timeBased()
    .everyMinutes(5)
    .create();
  console.log("Created one five-minute trigger for triggerCryptoSignalWorkflow.");
}

/** Removes only this project's trigger for the watchdog handler. */
function deleteWorkflowTriggers() {
  const handler = "triggerCryptoSignalWorkflow";
  ScriptApp.getProjectTriggers().forEach((trigger) => {
    if (trigger.getHandlerFunction() === handler) {
      ScriptApp.deleteTrigger(trigger);
    }
  });
  console.log("Removed existing watchdog triggers, if any.");
}

function getConfig_() {
  const props = PropertiesService.getScriptProperties();
  const token = (props.getProperty("GITHUB_TOKEN") || "").trim();
  const owner = (props.getProperty("GITHUB_OWNER") || GAS_WATCHDOG.owner).trim();
  const repo = (props.getProperty("GITHUB_REPO") || GAS_WATCHDOG.repo).trim();
  const workflow = (props.getProperty("GITHUB_WORKFLOW") || GAS_WATCHDOG.workflow).trim();
  const ref = (props.getProperty("GITHUB_REF") || GAS_WATCHDOG.ref).trim();

  if (!token) throw new Error("Missing Script Property: GITHUB_TOKEN");
  if (!owner || !repo || !workflow || !ref) {
    throw new Error("One or more GitHub target Script Properties are empty.");
  }

  return { token, owner, repo, workflow, ref };
}

function githubHeaders_(cfg) {
  return {
    Authorization: "Bearer " + cfg.token,
    Accept: "application/vnd.github+json",
    "X-GitHub-Api-Version": GAS_WATCHDOG.githubApiVersion,
    "Cache-Control": "no-cache",
  };
}

/** GET is safe to retry briefly for transient network/5xx/429 errors. */
function fetchWorkflowRuns_(cfg) {
  const url = "https://api.github.com/repos/" +
    encodeURIComponent(cfg.owner) + "/" + encodeURIComponent(cfg.repo) +
    "/actions/workflows/" + encodeURIComponent(cfg.workflow) +
    "/runs?branch=" + encodeURIComponent(cfg.ref) +
    "&per_page=" + GAS_WATCHDOG.runsPerPage;

  const options = {
    method: "get",
    headers: githubHeaders_(cfg),
    muteHttpExceptions: true,
  };

  let response = null;
  let lastError = null;
  for (let attempt = 1; attempt <= 3; attempt++) {
    try {
      response = UrlFetchApp.fetch(url, options);
      const code = response.getResponseCode();
      if ((code === 429 || code >= 500) && attempt < 3) {
        Utilities.sleep(1000 * attempt);
        continue;
      }
      break;
    } catch (err) {
      lastError = err;
      if (attempt < 3) Utilities.sleep(1000 * attempt);
    }
  }

  if (!response) {
    throw new Error("GitHub runs request failed: " + errorText_(lastError));
  }

  const code = response.getResponseCode();
  const body = response.getContentText() || "";
  if (code !== 200) {
    throw new Error("GitHub runs check failed (HTTP " + code + "): " + body.slice(0, 600));
  }

  const payload = JSON.parse(body || "{}");
  const runs = Array.isArray(payload.workflow_runs) ? payload.workflow_runs : [];
  // Sort explicitly newest first rather than relying on API ordering.
  runs.sort((a, b) => toMillis_(b.created_at) - toMillis_(a.created_at));
  return runs;
}

/** POST is deliberately not retried immediately: a timed-out POST may have
 * reached GitHub. The next five-minute check will see an active run if it did. */
function dispatchWorkflow_(cfg) {
  const url = "https://api.github.com/repos/" +
    encodeURIComponent(cfg.owner) + "/" + encodeURIComponent(cfg.repo) +
    "/actions/workflows/" + encodeURIComponent(cfg.workflow) + "/dispatches";

  const response = UrlFetchApp.fetch(url, {
    method: "post",
    headers: githubHeaders_(cfg),
    contentType: "application/json",
    payload: JSON.stringify({ ref: cfg.ref }),
    muteHttpExceptions: true,
  });

  const code = response.getResponseCode();
  const body = response.getContentText() || "";
  if (code !== 204) {
    throw new Error("GitHub dispatch failed (HTTP " + code + "): " + body.slice(0, 600));
  }
}

function isActiveRun_(run) {
  return ["queued", "in_progress", "waiting", "requested", "pending"]
    .indexOf(String(run.status || "").toLowerCase()) !== -1;
}

function notifyIfRunIsStuck_(run) {
  const ageMinutes = runAgeMinutes_(run);
  if (ageMinutes === null) return;

  const status = String(run.status || "").toLowerCase();
  const limit = status === "queued"
    ? GAS_WATCHDOG.queuedAlertAfterMinutes
    : GAS_WATCHDOG.runningAlertAfterMinutes;

  if (ageMinutes < limit) return;

  const link = run.html_url || "(رابط التشغيل غير متاح)";
  sendAlertOnce_(
    "active-workflow-too-long",
    "Crypto monitor: تشغيل GitHub طال أكثر من المتوقع",
    "ما زال workflow نشطًا ولم يطلق Google Apps Script تشغيلًا موازيًا.\n" +
    "رقم التشغيل: #" + run.run_number + "\n" +
    "الحالة: " + status + "\n" +
    "العمر التقريبي: " + Math.floor(ageMinutes) + " دقيقة\n" +
    "الرابط: " + link + "\n\n" +
    "تحقق من سجل Actions. مهلة job الموصى بها في monitor.yml هي 20 دقيقة."
  );
}

function inspectLatestCompletedRun_(runs) {
  const latest = runs.find((run) => run.status === "completed" && run.conclusion);
  if (!latest) return;

  if (latest.conclusion === "success") {
    sendRecoveryIfNeeded_(latest);
    return;
  }

  sendAlertOnce_(
    "workflow-completed-unsuccessfully",
    "Crypto monitor: فشل تشغيل GitHub Actions",
    "آخر تشغيل مكتمل لم ينتهِ بنجاح.\n" +
    "رقم التشغيل: #" + latest.run_number + "\n" +
    "النتيجة: " + latest.conclusion + "\n" +
    "وقت آخر تحديث: " + (latest.updated_at || "غير معروف") + "\n" +
    "الرابط: " + (latest.html_url || "غير متاح") + "\n\n" +
    "سيحاول Google Apps Script إطلاق دورة جديدة عندما لا يبقى تشغيل نشط."
  );
}

function sendAlertOnce_(key, subject, body) {
  const email = getAlertEmail_();
  if (!email) {
    console.log("ALERT (no ALERT_EMAIL configured): " + subject + " | " + body);
    return;
  }

  const props = PropertiesService.getScriptProperties();
  const now = Date.now();
  const lastKey = props.getProperty("ALERT_LAST_KEY") || "";
  const lastAt = Number(props.getProperty("ALERT_LAST_AT_MS") || 0);
  const configuredCooldown = Number(props.getProperty("ALERT_COOLDOWN_MINUTES") ||
                                     GAS_WATCHDOG.defaultAlertCooldownMinutes);
  const cooldownMs = Math.max(1, configuredCooldown) * 60 * 1000;

  if (lastKey === key && (now - lastAt) < cooldownMs) {
    console.log("Email alert rate-limited for incident: " + key);
    return;
  }

  try {
    MailApp.sendEmail(email, subject, body);
    props.setProperty("ALERT_LAST_KEY", key);
    props.setProperty("ALERT_LAST_AT_MS", String(now));
    props.setProperty("ALERT_OPEN_KEY", key);
    props.setProperty("ALERT_OPENED_AT_MS", String(now));
    console.log("Alert email sent to configured recipient for: " + key);
  } catch (err) {
    // Do not hide the original GitHub error if email itself is unavailable.
    console.error("Could not send alert email: " + errorText_(err));
  }
}

function sendRecoveryIfNeeded_(successfulRun) {
  const props = PropertiesService.getScriptProperties();
  const openKey = props.getProperty("ALERT_OPEN_KEY");
  const openedAt = Number(props.getProperty("ALERT_OPENED_AT_MS") || 0);
  if (!openKey || !openedAt) return;

  const email = getAlertEmail_();
  if (!email) return;

  const completedAt = toMillis_(successfulRun.updated_at || successfulRun.created_at);
  if (!completedAt || completedAt <= openedAt) return;

  try {
    MailApp.sendEmail(
      email,
      "Crypto monitor: عاد التشغيل بنجاح",
      "اكتمل تشغيل GitHub Actions بنجاح بعد التنبيه السابق.\n" +
      "رقم التشغيل: #" + successfulRun.run_number + "\n" +
      "وقت الانتهاء المسجل: " + (successfulRun.updated_at || "غير معروف") + "\n" +
      "الرابط: " + (successfulRun.html_url || "غير متاح")
    );
    props.deleteProperty("ALERT_OPEN_KEY");
    props.deleteProperty("ALERT_OPENED_AT_MS");
    props.deleteProperty("ALERT_LAST_KEY");
    props.deleteProperty("ALERT_LAST_AT_MS");
    console.log("Recovery email sent; cleared open alert state.");
  } catch (err) {
    console.error("Could not send recovery email: " + errorText_(err));
  }
}

function getAlertEmail_() {
  return (PropertiesService.getScriptProperties().getProperty("ALERT_EMAIL") || "").trim();
}

function runAgeMinutes_(run) {
  const start = toMillis_(run.run_started_at || run.created_at);
  if (!start) return null;
  return Math.max(0, (Date.now() - start) / 60000);
}

function toMillis_(isoText) {
  if (!isoText) return 0;
  const value = Date.parse(isoText);
  return Number.isFinite(value) ? value : 0;
}

function errorText_(err) {
  if (!err) return "Unknown error";
  const text = err.stack ? String(err.stack) : String(err);
  return text.slice(0, 1500);
}
