// WebGuard - MV3 service worker. Measures time on the active tab per host.
// report() only logs for now; POST /usage to the daemon comes in step 4.

const ALARM_NAME = "webguard-tick";
const IDLE_SECONDS = 20;
const ALARM_SECONDS = 30; // Chrome clamps the alarm period to this floor anyway
const FLUSH_MIN_MS = 1000;

// storage.session (memory-backed, survives worker suspension):
// { seg: { host, startedAt } | null, pending: { [host]: ms } }
async function loadState() {
  const { wg } = await chrome.storage.session.get("wg");
  return wg ?? { seg: null, pending: {} };
}

async function saveState(state) {
  await chrome.storage.session.set({ wg: state });
}

// Plain hostname; normalisation to eTLD+1 is the daemon's job (core/domains.py).
// null for anything we must not measure (chrome://, file://, about:blank, ...).
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

// A still-audible tab counts even while chrome.idle reports "idle" - idle only
// watches keyboard/mouse, not a playing video. "locked" always pauses.
function isCountable(view) {
  if (!view.windowFocused) return false;
  if (!view.host) return false;
  if (view.idleState === "locked") return false;
  if (view.idleState === "idle" && !view.audible) return false;
  return true;
}

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

// Close the running segment, credit its time, open the next one.
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
    state.pending[host] = ms - seconds * 1000; // carry the sub-second remainder
    report(host, seconds);
  }
  for (const [host, ms] of Object.entries(state.pending)) {
    if (ms === 0) delete state.pending[host];
  }
}

function report(host, seconds) {
  // Step 4: POST http://127.0.0.1:<port>/usage { host, delta_seconds: seconds }
  console.log(`[webguard] ${host} +${seconds}s`);
}

// Events only settle segments; reporting happens on the alarm tick so the
// cadence stays ~30s however chatty the page is. Serialised - storage.session
// has no locking. An alarm (not setInterval) because the worker can be evicted.
let queue = Promise.resolve();
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

// The worker may be revived by an event above rather than by init; make sure
// the alarm still exists in that case.
chrome.alarms.get(ALARM_NAME).then((a) => {
  if (!a) init();
});
