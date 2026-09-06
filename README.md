# WebGuard

Self-control tool for Windows. It measures the time you spend on chosen websites
and blocks them once a daily limit is exceeded (20 minutes by default).

Personal project, single user, Windows.

## Design principles

### 1. Friction, not infallibility

You are the administrator of your own machine, so the block **cannot** be truly
unbypassable — there is always safe mode, an admin account, a live USB. That is
accepted. The goal is to make a bypass cost 15–20 minutes of deliberate, tedious
work instead of one click. The "just 2 minutes" impulse does not survive 15
minutes of digging through Windows services.

### 2. Ulysses contract

There is no unlock password and no user-facing emergency mode. Instead:

- **tightening** a rule (shorter limit, adding a site, cancelling a removal)
  takes effect **immediately**
- **loosening** (longer limit, removing a rule) takes effect after **24 hours**

The `pending_limit_seconds`, `pending_effective_at` and `remove_effective_at`
columns in the `rules` table exist solely for this.

## Architecture

Three clearly separated processes:

1. **Browser extension (Manifest V3)** — time measurement. The only component
   that knows which tab is active. It does not count time while the window is in
   the background or the user is idle. Reports incrementally every ~5s.
2. **Daemon (Windows service, LocalSystem)** — source of truth. All state and all
   decisions live here. The extension and GUI are thin clients that receive
   decisions, they do not make them.
3. **GUI (Tauri + React + TS)** — configuration: site list, limits, statistics.
   No database access; every change is validated by the daemon, including the
   refusal of an immediate loosening.

The daemon exposes HTTP on `127.0.0.1`, split into two trust channels:

- **ingest**, no auth: `POST /usage`, `GET /status`. The worst a malicious page
  can do is report fake seconds and block something for itself.
- **admin**, token from `meta.admin_token`: everything touching rules and limits.

The limit key is the **eTLD+1 domain**, not the full URL, so `www.youtube.com`,
`m.youtube.com` and `youtube.com/watch?v=x` share one limit. The daily window
resets at 04:00, not midnight.

### Enforcement layers

The block must not depend on the extension, because the extension is disabled
with a single click.

| Layer | Role |
|---|---|
| `declarativeNetRequest` in the extension | Precision (per URL and path), nice block page |
| `hosts` file | Works in every browser and in incognito |
| Chrome Enterprise Policy (HKLM) | Forces extension install, blocks removal |
| Service watchdog + recovery | Comes back after the process is killed |

## Stack

| Layer | Choice |
|---|---|
| Daemon | Python 3.11+ |
| Database | SQLite (`sqlite3` from stdlib, no ORM) |
| Windows service | WinSW |
| Extension | Manifest V3, plain JS |
| GUI | Tauri v2 + React + TS |
| Packaging | PyInstaller onefile |

## Status

- [x] **Step 1 — core**: day boundary (`core/clock.py`), URL → eTLD+1
  normalization (`core/domains.py`), SQLite storage with the 24h-delay logic
  (`core/storage.py`). Tests pass.
- [ ] Step 2 — extension + time measurement
- [ ] Step 3 — enforcement (`hosts` + `declarativeNetRequest`)
- [ ] Step 4 — daemon as a Windows service (WinSW, ACL, watchdog)
- [ ] Step 5 — GUI (Tauri + React)

## Development

```sh
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python -m pytest -v
```

Type checking: `.venv\Scripts\pyrefly check` (config in `pyrefly.toml`).
