# WorkBuddy Credit Widget

**English** · [简体中文](README.md)

> A tiny always-on-top capsule that keeps your WorkBuddy credit balances, today's burn, and expiry risk visible at a glance — for **all accounts at once**. It appears when the client opens and disappears when the client closes.
> **Zero third-party dependencies. One file. Pure Python standard library.**

![Python](https://img.shields.io/badge/Python-3.9%2B-3776AB?logo=python&logoColor=white)
![Platform](https://img.shields.io/badge/Platform-Windows-0078D4?logo=windows&logoColor=white)
![Dependencies](https://img.shields.io/badge/dependencies-0-brightgreen)
![Core](https://img.shields.io/badge/core-1%20file%20~2.8k%20lines-blue)
![License](https://img.shields.io/badge/license-MIT-green)

![Capsule and credit preview panel](assets/01-capsule-and-preview.png)

---

## The problem

WorkBuddy credits are issued monthly, **expire at the end of the cycle, and do not roll over**. If you switch between several accounts, three things go wrong:

| Pain | Consequence |
|---|---|
| Balances are buried inside the client | You discover a large expired batch at month end |
| Only one account can be signed in at a time | Every other account is completely invisible |
| No idea how long the balance will last | You either under-use it or suddenly run out |

This widget compresses **balances, today's spend, and the next expiry date for every account into one ~800px capsule**.

---

## Features

| | |
|---|---|
| **Multi-account rollup** | Scans local login snapshots, de-duplicates by uid, shows them all |
| **Three-tier refresh** | 15s balance-only for all accounts / 60s full refresh of the current one / 600s full rollup |
| **Rotation** | The capsule cycles through accounts every 10s (`⟳ 3/5`) |
| **Expiry warnings** | Amber border + blinking `⚠` when a batch expires within 3 days, with 3d / 24h / 6h tiered reminders |
| **Runout forecast** | Estimates daily burn from the last 7 days and shows “~4d 12h left” |
| **Change tick** | A red `+N` flashes next to *today* when spend is detected |
| **Follows the client** | A headless watcher starts at logon; the window only appears while the client is open |
| **No dependencies** | Needs nothing but Python + tkinter — nothing to break later |

---

## Screenshots

> All screenshots use **synthetic data** — no real accounts, balances, or expiry dates.

Click the capsule to open the credit preview panel:

![Credit preview panel](assets/01-capsule-and-preview.png)

Forecast warning when the balance is running out:

![Runout alert](assets/02-runout-alert.png)

Per-batch reminders before credits expire:

![Expiry alert](assets/03-expiry-alert.png)

A one-shot summary pops up when the client starts:

![Startup summary](assets/04-startup-summary.png)

---

## Quick start

### 0. Get the code

```bat
git clone https://github.com/FBR-123/workbuddy-credit-viewer
cd workbuddy-credit-viewer
```

(No Git? Use **Code → Download ZIP** on the repo page.)

**Requirements:** Windows 10/11 · Python 3.9+ **with tkinter** (the python.org installer includes it) · a signed-in WorkBuddy client.

> [!IMPORTANT]
> Use a full Python from python.org. Stripped “managed” Pythons shipped by other tools usually **have no tkinter**, and the widget cannot start. Check with `python -c "import tkinter"`.

### 1. No paths to edit

The launchers find Python themselves, in this order:

1. the `WB_PYTHONW` environment variable
2. `%LOCALAPPDATA%\Programs\Python\*\pythonw.exe` (per-user install — the usual case)
3. `%ProgramFiles%\*\pythonw.exe`, `%ProgramFiles%\Python\*\pythonw.exe` (all-users install)

Every candidate is probed with `import tkinter`, so a stripped interpreter is skipped rather than silently breaking the widget. Only if nothing is found do you need to set it manually:

```bat
setx WB_PYTHONW "C:\Users\you\AppData\Local\Programs\Python\Python313\pythonw.exe"
```

### 2. Run it once

```bat
start_widget.vbs
```

The capsule shows up in the top-right corner and its border flashes for 3.5s. **First data takes 5–15 seconds** (one round trip per account). Right-click → *reset to bottom-right* if you cannot find it.

### 3. Make it follow the client

```bat
autostart_on.vbs
```

or right-click the capsule → *start with WorkBuddy* → enable.

From then on **nothing appears at logon**; the capsule only shows up once WorkBuddy is open.

Undo everything:

```bat
autostart_off.vbs
```

### 4. Diagnostics

```bat
python wb_credit_widget.py --version
python wb_credit_widget.py --autostart status   :: autostart / watcher / widget / client
python wb_credit_widget.py --dump               :: print one summary, no UI
python wb_credit_widget.py --stop-all           :: stop widget + watcher
python tests\diagnose_dead.py                   :: on-site diagnosis
```

---

## Reading the capsule

```
● ⟳ 189****3009 3/5 │ 余额 5,666 │ 今日 0 │ 3日到期 322 │ 5日 542  ≈耗尽约6天7时  8s前
```

| Part | Meaning |
|---|---|
| `●` | Data health |
| `⟳ account N/M` | Rotation is on; this is account N of M |
| `余额` / *balance* | Balance of the account currently shown |
| `今日` / *today* | Spend across **all** accounts; a red `+N` flashes on change |
| `3日到期` / *expiring ≤3d* | Amount that clears within 3 days — the capsule turns amber and `⚠` blinks |
| `≈耗尽约X天Y时` | Projected runout time, only shown below the threshold |
| `8s前` | Time since the last successful refresh |

**Mouse:** click = preview panel · drag = move (position is remembered) · right-click = 43-item menu.

### Configuration

Edit `%LOCALAPPDATA%\WBCreditWidget\config.json`, or use the right-click menu:

| Key | Default | Meaning |
|---|---|---|
| `poll_seconds` | 15 | Balance-only refresh for all accounts |
| `refresh_seconds` | 60 | Full refresh of the current account |
| `full_seconds` | 600 | Full rollup of all accounts |
| `rotate_seconds` | 10 | Rotation interval, `0` = off |
| `follow_workbuddy` | `true` | Hide while the client is closed |
| `exit_with_workbuddy` | `true` | Exit when the client closes (watcher-launched instances only) |
| `runout_days` | 7 | Warn when ≤ N days left, `0` = off |
| `alert_days` | 3 | Expiry warning threshold |

### Data & privacy

- Credentials are read from the local login state **at call time** — never written to disk, never uploaded, never logged;
- Account identifiers are always masked (`189****3009`), including in logs and `state.json`;
- Only three kinds of local files exist: `config.json`, `state.json`, `*.log`, all under `%LOCALAPPDATA%\WBCreditWidget\`;
- Delete that folder and it is gone.

---

## How it works

```
logon
 └─► Startup\WorkBuddyCreditWidget.vbs   (ASCII, 6s delay)
        └─► pythonw wb_credit_widget.py --watch     ← headless, no network of its own
               ├─ client window appears ──► spawn --managed ──► capsule shown
               └─ client window disappears ──► 15s grace ──► child terminated
```

Two design decisions came out of real failures:

**1. “Is the client open?” is answered by windows, not processes.**
After you close the client window, its `daemon`, `sidecar`, `codebuddy --prewarm` pools and a `--gc_retain_sec 600` job container **keep running for up to 10 minutes — with the same image name, `WorkBuddy.exe`**. A process-based check keeps saying “still open”, so the widget never exits. The check is therefore `EnumWindows` + `IsWindowVisible`. (Minimising is *not* closing: Electron does not hide the window when minimised.)

**2. Single-instance detection uses process *identity*, not “is the pid alive”.**
After a hard shutdown the pid file cannot be cleaned up, and that pid gets reused by an unrelated process (observed: `svchost.exe`). A naive liveness check returns a false positive, the new instance concludes “an instance is already running”, and exits silently — which users experience as **“sometimes nothing appears at logon”**. The pid file therefore stores `pid` *plus the process creation time*, and readers verify both.

More implementation detail (refresh algorithm, runout maths, every trade-off): [docs/详细使用手册.md](docs/详细使用手册.md) (Chinese).

---

## Known limitations

| Limitation | Detail |
|---|---|
| **Windows only** | Heavy use of `ctypes` against Win32 (process, window, GDI) |
| **Needs a signed-in client** | By design — this is not a defect |
| **Reverse-engineered endpoints** | Internal billing APIs; the vendor may change them at any time |
| **Some accounts may return 401** | Expired login state; sign that account in again |
| **Unofficial** | Not affiliated with WorkBuddy / CodeBuddy — see the disclaimer |

---

## Project layout

```
credit_widget/
├── wb_credit_widget.py     # app: UI / fetching / scheduling / watcher / autostart / pid identity
├── shot_widget.py          # screenshot helper (pure ctypes + zlib PNG, no Pillow)
├── _findpy.bat             # Python discovery (requires tkinter), shared by all launchers
├── start_widget.bat/.vbs   # start now
├── watch_widget.bat        # run the watcher only
├── stop_widget.bat         # stop widget + watcher
├── autostart_on/off.bat/.vbs
├── assets/                 # screenshots
├── docs/                   # detailed manual (zh) + release copy (zh)
├── tests/                  # 8 test suites + 3 static checks + 1 diagnostic
└── .github/workflows/ci.yml
```

> The `.vbs` files intentionally contain **no logic** — they only run the matching `.bat` with the console hidden. On machines where the Windows script host is blocked, batch is the layer that can still be executed and tested.

---

## Tests

```bat
tests\run_tests.bat
```

| Suite | Asserts | Covers |
|---|---:|---|
| `test_window_detection.py` | 12 | Remaining processes without a window must count as “closed”; minimising must not |
| `test_watch_logic.py` | 21 | Per-tick decisions, cooldown, no false kills on flapping, atomic autostart writes |
| `test_pid_identity.py` | 18 | Pid reuse: wrong image or creation time ⇒ treated as stale and removed |
| `test_lifecycle.py` | 6 | `--managed` exits gracefully (verified by a marker file) |
| `test_menu_smoke.py` | 6 | 43 menu entries build; actions do not raise |
| `test_pipeline_e2e.py` | 13 | Full cycle: appear → spawn → disappear → exit → respawn |
| `test_stale_pid_recovery.py` | 6 | Three kinds of corrupt pid files must still allow a clean start |
| `test_real_window_flow.py` | 13 | Real window destroyed while process stays alive ⇒ widget must exit |
| `check_autostart_vbs.py` | 8 | Autostart entry: ASCII, no BOM, paths exist, carries `--watch` |
| `check_vbs_syntax.py` | 27 | All `.vbs`: structure, `Option Explicit` variables, refs, **no logic** |
| `check_no_secrets.py` | — | Pre-push audit: credentials, full phone numbers, e-mails, absolute paths |

Every test uses an **isolated temporary data directory** — it never touches your real `config.json` / `state.json`.

CI runs the non-GUI subset on Windows across Python 3.9 / 3.12 / 3.13. The GUI and end-to-end suites create real windows and assert against a running client, so they are local-only by design.

---

## Security notes

- **No credentials are stored.** Tokens are read from the local login state at call time.
- **The only third-party code path** is the `workbuddy_checkin` helper (used to decrypt the token envelope). It is only loaded from `~/.workbuddy/skills/*checkin*/scripts/` — never from arbitrary skill folders, which would let any installed skill execute code at startup. Override with `WB_CHECKIN_SKILL_DIR` if it lives elsewhere; without it the widget still works.
- **`--stop` / `--stop-all` verify process identity** (image name + creation time) before killing anything, so a reused pid can never take down an unrelated process.

---

## Disclaimer

- Personal, unofficial tool. **Not affiliated with WorkBuddy / CodeBuddy.**
- Credit data is read from the vendor's API through your own local login state and **belongs to the vendor**; nothing is stored, uploaded, or redistributed.
- Internal endpoints carry no stability guarantee and may break after an update.
- Use it only to view **your own** account balances. Do not use it for bulk, high-frequency, or any purpose that violates the terms of service. Use at your own risk.

---

## License

[MIT](LICENSE)
