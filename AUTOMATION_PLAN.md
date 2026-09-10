# Seamless auto-export (Day One → this app)

Removes the last manual step — exporting your Day One journal with media — so
entries flow in with **zero taps**, via a job that runs nightly while you sleep.
Built and working on Windows; runs on macOS too.

## The decision, in short

Two routes were investigated:

- **Reading Day One's local database directly — RULED OUT on Windows.** The
  Windows Store app keeps no readable database (≈300 extensionless blobs + a
  few `.dat`/`.log`/`.json` files — a proprietary store). The Mac app *does*
  keep a readable SQLite store, so this could be revisited on a Mac, but it's
  not worth the fragility when the browser route works everywhere.
- **Automating the Day One WEB export — CHOSEN.** dayone.me exports the journal
  as Day One's official JSON **with media**, its export dialog is reachable by a
  deep-link URL, and the whole export is one button. It produces the exact
  format `ingest.py` already parses, and it's device-independent (the web has
  everything that has synced, no matter where you journaled).

## The pieces (all in this folder)

### `dayone_web_export.py` — does the export

One command handles everything: opens Day One web in a saved browser profile,
waits out any first-time sync, logs you in if needed, opens the export dialog,
clicks the single "Export journal JSON file" button, and saves the download
straight into your watched folder (`config.EXPORT_WATCH_DIR`) with **no Save
dialog** (it configures Chrome to auto-save there). The watcher ingests it.

Modes:
- `python dayone_web_export.py` — normal, visible run. **Do this once first** and
  log into Day One in the window; the login is saved in `dayone_profile/` and
  reused forever after.
- `python dayone_web_export.py nightly` — unattended (what the scheduler runs).
  It opens a **real browser window** (briefly visible while you sleep), not a
  headless one — Day One's web app, especially its media download, is
  unreliable headless but rock-solid in a real browser. If the saved session
  has expired it fails fast and pops a desktop notification asking for one
  interactive login.
- `python dayone_web_export.py warmup` — optional; holds the window open so a
  giant first sync can finish. Usually unnecessary.

Requirements (one time):
```
pip install playwright
python -m playwright install chromium
```
(If `playwright` isn't on PATH, the module form works: `python -m playwright install chromium`.)

### `schedule_export.py` — sets up the nightly job

The **setup wizard has a one-click button** for this now ("Step 6: Schedule the
nightly export"), which just runs this script for you — most people should use
that. You can also run it directly:
```
python schedule_export.py             # install, 3 AM default
python schedule_export.py --time 02:30 # install at a specific time
python schedule_export.py --remove     # uninstall
```
- **Windows:** a Task Scheduler task ("MiniAI DayOne Auto-Export") that runs
  daily via `pythonw` (silent, no window), set to **wake the PC from sleep** and
  to run when next available if the PC was off. If Windows refuses without
  admin, it triggers a UAC prompt automatically.
- **macOS:** a launchd agent at the chosen time. Note: macOS won't wake a
  closed-lid Mac for it — it runs next time the Mac is awake. To force a wake,
  the installer prints a `pmset repeat wakeorpoweron ...` line you can run.

## Setup order (when you're ready to go live)

1. `pip install playwright` and `python -m playwright install chromium`.
2. `python dayone_web_export.py` once — log into Day One, let it export. Confirm
   a file lands in your watched folder and the watcher ingests it.
3. `python schedule_export.py` — schedule the nightly run.
4. (Optional) test the scheduled task immediately: Task Scheduler Library →
   right-click "MiniAI DayOne Auto-Export" → Run.

That's it — from then on it exports and ingests itself every night.

## Honest caveats

- The computer must be **on or asleep** at the scheduled time (not shut down).
  Windows wakes from sleep; a closed-lid Mac won't unless you add the `pmset`
  wake.
- The saved **web session** must stay valid. If Day One logs it out, the nightly
  run fails and notifies you; the fix is one interactive
  `python dayone_web_export.py` to log in again.
- `dayone_profile/` holds that logged-in session and is **git-ignored** — it must
  never be committed.
- First sync into a fresh profile is the slow one; later runs only pull what's
  new, so they're quick.

## Not pursued (and why)

- **Diarium:** no web app to automate, but it already syncs continuously to a
  cloud folder — so it needs none of this.
- **On-phone automation:** unnecessary; the web export already captures whatever
  synced from the phone, and iOS can't schedule Day One's export anyway.
- **AI-vision clicking / auto-revising code from forums:** kept as a possible
  robustness fallback, not the primary design — the deep-link + one-button flow
  is stable, and auto-applied code changes to a working system add more risk
  than they remove. Fix it if/when Day One actually changes the flow.
