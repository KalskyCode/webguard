// WebGuard - browser extension (MV3 service worker)
// Step 2: measure active-tab time and report it. For now it only logs to the
// console; POST /usage to the daemon lands in a later step.
//
// The model
// ---------
// Time is measured in "segments". A segment is a stretch of wall-clock time
// during which exactly one host was being looked at and the time counted:
//
//     countable  <=>  a browser window is focused
//                 AND  the user is not idle/locked
//                 AND  the active tab is a real http(s) page
//
// Any event that could change that predicate calls settle(): it closes the
// running segment, credits its milliseconds to `pending[host]`, and opens a new
// segment if we are still counting. A periodic alarm does the same, so a page
// left open with no events still gets credited and flushed.
//
// Why an alarm and not setInterval: an MV3 service worker is killed after ~30s
// idle, which would stop setInterval. chrome.alarms survives suspension and
// wakes the worker. Chrome clamps the alarm period to 30s minimum, so that is
// our worst-case reporting lag - CLAUDE.md's "~5s" is not reachable this way.
//
// Why chrome.storage.session for state: in-memory globals do not survive worker
// suspension. storage.session is memory-backed (never hits disk), cleared when
// the browser closes, and shared across worker restarts - exactly what we need.

const ALARM_NAME = "webguard-tick";
const IDLE_SECONDS = 20; // chrome.idle threshold (min honoured value is 15)
const ALARM_SECONDS = 30; // Chrome clamps the alarm period to this floor anyway
const FLUSH_MIN_MS = 1000; // do not report sub-second dust

// --- state in storage.session --------------------------------------------------
// {
//   seg: { host: string, startedAt: number } | null,   // startedAt = Date.now() ms
//   pending: { [host: string]: number }                 // unflushed milliseconds
// }

async function loadState() {
  const { wg } = await chrome.storage.session.get("wg");
  return wg ?? { seg: null, pending: {} };
}

async function saveState(state) {
  await chrome.storage.session.set({ wg: state });
}

// --- turning a tab URL into a measurable host -------------------------------
// Plain hostname like "www.youtube.com"; normalisation to eTLD+1 is the
// daemon's job (core/domains.py), not ours. null for anything we must not
// measure: chrome://, chrome-extension://, file://, about:blank, the new-tab
// page, empty/undefined.
function hostFromUrl(url) {
  if (!url) return null;
  let u;
  try {
    u = new URL(url);
  } catch {
    return null;
  }
  if (u.protocol !== "http:" && u.protocol !== "https:") return null;
  return u.hostname || null;
}

// isCountable(view): given { windowFocused, idleState, host, audible } return
// true iff a segment should be running. idleState is "active" | "idle" |
// "locked". Per decision: a still-audible tab (video/music playing) counts even
// while chrome.idle reports "idle", because chrome.idle only watches the
// keyboard and mouse. "locked" always pauses.
function isCountable(view) {
  if (!view.windowFocused) return false;
  if (!view.host) return false;
  if (view.idleState === "locked") return false;
  if (view.idleState === "idle" && !view.audible) return false;
  return true;
}

// --- reading the current world ----------------------------------------------
// One place that asks Chrome "what is on screen right now", so every event
// handler and the alarm go through the same path.
async function currentView() {
  const [win, idleState] = await Promise.all([
    chrome.windows.getLastFocused({ populate: false }).catch(() => null),
    chrome.idle.queryState(IDLE_SECONDS),
  ]);

  const windowFocused = !!win && win.focused;
  let host = null;
  let audible = false;
  if (windowFocused) {
    const [tab] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
    if (tab) {
      host = hostFromUrl(tab.url);
      audible = !!tab.audible;
    }
  }
  return { windowFocused, idleState, host, audible };
}

// --- the core: close the open segment, open the next one --------------------
async function settle(state, now = Date.now()) {
  const prevHost = state.seg ? state.seg.host : null;

  if (state.seg) {
    const elapsed = now - state.seg.startedAt;
    if (elapsed > 0) {
      state.pending[state.seg.host] = (state.pending[state.seg.host] ?? 0) + elapsed;
    }
    state.seg = null;
  }

  const view = await currentView();
  const nextHost = isCountable(view) ? view.host : null;
  if (nextHost) {
    state.seg = { host: nextHost, startedAt: now };
  }

  if (prevHost !== nextHost) {
    console.log(`[webguard] ${prevHost ?? "(paused)"} -> ${nextHost ?? "(paused)"}`);
  }
}

async function flush(state) {
  for (const [host, ms] of Object.entries(state.pending)) {
    if (ms < FLUSH_MIN_MS) continue;
    const seconds = Math.floor(ms / 1000);
    state.pending[host] = ms - seconds * 1000; // carry the remainder
    report(host, seconds);
  }
  for (const [host, ms] of Object.entries(state.pending)) {
    if (ms === 0) delete state.pending[host]; // do not let the object grow forever
  }
}

function report(host, seconds) {
  // Step 4 replaces this with: POST http://127.0.0.1:<port>/usage
  // body { host, delta_seconds: seconds }. The daemon owns the clock and the
  // eTLD+1 normalisation; we only say "this host got N more seconds".
  console.log(`[webguard] ${host} +${seconds}s`);
}

// --- one entry point every trigger funnels into ---------------------------
// Events only close/open segments (settle). Reporting happens on the alarm
// tick, so the report cadence stays ~30s regardless of how chatty a page is.
let queue = Promise.resolve(); // serialise; storage.session has no locking
function schedule(label, doFlush) {
  queue = queue
    .then(async () => {
      const state = await loadState();
      await settle(state);
      if (doFlush) await flush(state);
      await saveState(state);
    })
    .catch((err) => console.error(`[webguard] ${label} failed`, err));
  return queue;
}

const pump = (label) => schedule(label, false);

// --- wiring --------------------------------------------------------------
chrome.tabs.onActivated.addListener(() => pump("tabs.onActivated"));
chrome.tabs.onUpdated.addListener((_id, changeInfo) => {
  if (changeInfo.url || changeInfo.status === "complete") pump("tabs.onUpdated");
});
chrome.windows.onFocusChanged.addListener(() => pump("windows.onFocusChanged"));
chrome.idle.onStateChanged.addListener(() => pump("idle.onStateChanged"));
chrome.alarms.onAlarm.addListener((a) => {
  if (a.name === ALARM_NAME) schedule("alarm", true);
});

chrome.runtime.onStartup.addListener(init);
chrome.runtime.onInstalled.addListener(init);

async function init() {
  chrome.idle.setDetectionInterval(IDLE_SECONDS);
  await chrome.alarms.create(ALARM_NAME, { periodInMinutes: ALARM_SECONDS / 60 });
  await pump("init");
}

// A cold start can also be triggered by one of the event listeners above firing
// after the worker was evicted; make sure the alarm exists in that case too.
chrome.alarms.get(ALARM_NAME).then((a) => {
  if (!a) init();
});
