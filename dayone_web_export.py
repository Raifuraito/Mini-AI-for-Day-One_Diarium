"""
dayone_web_export.py  --  automated Day One web export

Exports your Day One journal (with media) from the Day One WEB app and drops it
straight into this project's watched folder, so the existing watcher ingests it
automatically. No manual clicking, no Save dialog. Runs on Windows and macOS.

MODES
-----
    python dayone_web_export.py            Normal run. Opens a visible window,
                                           waits out any first-time sync, logs
                                           you in if needed, exports.

    python dayone_web_export.py nightly    Unattended run for the scheduler.
                                           Headless (no window). Assumes you're
                                           already logged in; if the session has
                                           expired it fails fast and pops a
                                           desktop notification telling you to
                                           run a normal login once.

    python dayone_web_export.py warmup     Optional: open the journal and hold
                                           the window open so a huge first sync
                                           can finish at its own pace. Usually
                                           unnecessary -- a normal run waits for
                                           sync by itself.

FIRST TIME
----------
Run a normal `python dayone_web_export.py` once and log into Day One in the
window that opens. The login is saved in dayone_profile/ (next to this script)
and reused forever after -- including by the nightly job -- until Day One
eventually expires it, at which point you just do one more normal run to log in.

REQUIREMENTS
------------
    pip install playwright
    python -m playwright install chromium
"""

import os
import sys
import time
import json
import re
import subprocess

# Reuse THIS project's own config so the export lands exactly where the watcher
# already looks (whatever you set as your journal export folder in the wizard).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config

# --- Settings you can change -------------------------------------------------

# Your Day One journal's web id. Open your journal at https://dayone.me and copy
# the number that appears after /journals/ in the address bar.
JOURNAL_ID = "100716813777"

# Where the login/session is remembered between runs (kept next to this script).
PROFILE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dayone_profile")

# Where the downloaded export is saved -- this project's watched folder, pulled
# straight from your existing config so the watcher picks it up on its own.
OUTPUT_DIR = os.path.abspath(config.EXPORT_WATCH_DIR)

# How long a normal run waits for you to log in / for a first sync to finish.
LOGIN_WAIT_SECONDS = 240
# The nightly run assumes you're already logged in, so it waits only briefly
# before deciding the session has expired and bailing out with a notification.
NIGHTLY_READY_WAIT_SECONDS = 90

# -----------------------------------------------------------------------------

JOURNAL_URL = f"https://dayone.me/journals/{JOURNAL_ID}"
EXPORT_URL = (
    f"{JOURNAL_URL}?m2_name=edit_journal&m2_journal_id={JOURNAL_ID}"
    f"&m3_name=export&m3_journal_id={JOURNAL_ID}"
)


LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "export.log")


class _Tee:
    """Write to several streams at once (the console AND export.log), so
    everything a run prints -- progress and any error -- is also saved to a
    file you can open or share if something goes wrong."""

    def __init__(self, *streams):
        self.streams = streams

    def write(self, data):
        for s in self.streams:
            try:
                s.write(data)
            except Exception:
                pass

    def flush(self):
        for s in self.streams:
            try:
                s.flush()
            except Exception:
                pass


def _notify(title, message):
    """
    Pop a desktop notification so an unattended failure is never silent. Uses
    the OS's own notifier -- PowerShell toast on Windows, osascript on macOS --
    and quietly no-ops if neither is available (also prints to the log).
    """
    print(f"[{title}] {message}")
    try:
        if sys.platform == "win32":
            ps = (
                '[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime] | Out-Null;'
                '[Windows.UI.Notifications.ToastNotification, Windows.UI.Notifications, ContentType=WindowsRuntime] | Out-Null;'
                '[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom, ContentType=WindowsRuntime] | Out-Null;'
                '$t=[Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent('
                '[Windows.UI.Notifications.ToastTemplateType]::ToastText02);'
                f'$t.GetElementsByTagName("text")[0].AppendChild($t.CreateTextNode("{title}"))>$null;'
                f'$t.GetElementsByTagName("text")[1].AppendChild($t.CreateTextNode("{message}"))>$null;'
                '$n=[Windows.UI.Notifications.ToastNotification]::new($t);'
                '[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("MiniAI Journal").Show($n);'
            )
            subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                           capture_output=True, timeout=20)
        elif sys.platform == "darwin":
            script = f'display notification "{message}" with title "{title}"'
            subprocess.run(["osascript", "-e", script],
                           capture_output=True, timeout=20)
    except Exception:
        pass


def _ensure_no_download_prompt(profile_dir, download_dir):
    """
    Force Chrome (in our own profile) to auto-save downloads into download_dir
    with NO "Save As" dialog. This is what stops the native save prompt from
    appearing when Day One triggers the export in a popup tab.
    """
    prefs_dir = os.path.join(profile_dir, "Default")
    os.makedirs(prefs_dir, exist_ok=True)
    prefs_path = os.path.join(prefs_dir, "Preferences")

    prefs = {}
    if os.path.exists(prefs_path):
        try:
            with open(prefs_path, "r", encoding="utf-8") as f:
                prefs = json.load(f)
        except Exception:
            prefs = {}

    dl = download_dir.replace("\\", "/")  # Chrome accepts forward slashes here
    prefs.setdefault("download", {})
    prefs["download"]["prompt_for_download"] = False
    prefs["download"]["default_directory"] = dl
    prefs["download"]["directory_upgrade"] = True
    prefs.setdefault("savefile", {})
    prefs["savefile"]["default_directory"] = dl

    with open(prefs_path, "w", encoding="utf-8") as f:
        json.dump(prefs, f)


def _wait_for_new_export(download_dir, before_names, timeout_s):
    """
    Watch download_dir for a brand-new, finished file (Chrome names in-progress
    downloads *.crdownload, then renames on completion). Returns the new file's
    path, or None if nothing finished in time.
    """
    end = time.time() + timeout_s
    while time.time() < end:
        try:
            now = set(os.listdir(download_dir))
        except Exception:
            now = set()
        new = [
            n for n in (now - before_names)
            if not n.endswith(".crdownload") and not n.endswith(".tmp")
        ]
        if new:
            path = os.path.join(download_dir, new[0])
            try:
                s1 = os.path.getsize(path)
                time.sleep(2)
                if os.path.getsize(path) == s1:  # size stable -> finished
                    return path
            except Exception:
                pass
        time.sleep(1)
    return None


def _read_counts(page):
    """
    Pull the journal's live 'N Entries' / 'M Media' counts off the page. These
    climb as Day One syncs and go steady when it's done -- the honest signal for
    'fully synced', unlike the export button (which enables while sync is still
    finishing, which is how earlier exports came out incomplete). Returns
    (entries, media) or None if the numbers aren't on screen.
    """
    try:
        txt = page.inner_text("body")
    except Exception:
        return None
    e = re.search(r'([\d,]+)\s+Entries', txt)
    if not e:
        return None
    m = re.search(r'([\d,]+)\s+Media', txt)
    n = lambda s: int(s.replace(",", "")) if s else 0
    return (n(e.group(1)), n(m.group(1)) if m else 0)


def _wait_for_sync_complete(page, quiet_seconds, max_seconds):
    """
    Wait until the entries/media counts stop climbing for `quiet_seconds` in a
    row -- i.e. the sync has settled -- so the export we take next is complete.
    Reloads occasionally to force fresh counts. Falls through after max_seconds.
    """
    print("Waiting for your journal to finish syncing (counts must settle)...")
    start = time.time()
    last = None
    last_change = time.time()
    last_reload = start
    while time.time() - start < max_seconds:
        counts = _read_counts(page)
        if counts is not None:
            if counts != last:
                last = counts
                last_change = time.time()
                print(f"  syncing... {counts[0]} entries, {counts[1]} media", flush=True)
            elif time.time() - last_change >= quiet_seconds:
                print(f"  sync settled: {counts[0]} entries, {counts[1]} media")
                return
        # Nudge a reload every ~90s in case counts don't update live.
        if time.time() - last_reload > 90:
            try:
                page.goto(JOURNAL_URL, wait_until="domcontentloaded", timeout=60_000)
                page.get_by_text("Edit Journal", exact=False).first.wait_for(
                    state="visible", timeout=60_000)
            except Exception:
                pass
            last_reload = time.time()
        time.sleep(5)
    print("  (sync wait hit its time limit -- proceeding with what's synced)")


def _open_export_dialog(page):
    """
    Open Day One web's "Export Journal" dialog and return the export button.

    Primary path: load the deep-link URL *fresh* (blank the tab first) so the
    single-page app actually re-reads the URL and pops the dialog. Navigating to
    that URL from an already-open journal page sometimes doesn't trigger it --
    which is how the run could end up stuck on the main page with no button.

    Fallback: click through the UI (Edit Journal -> Journal Settings ->
    Export Journal), the same path a person would take.
    """
    def _dismiss_overlays():
        # Day One sometimes leaves a modal backdrop on top
        # (components-modal__screen-overlay) that silently *intercepts clicks*
        # -- the exact failure that made a nightly run time out. A few Escape
        # presses close whatever stray dialog is open; harmless if none is.
        for _ in range(3):
            try:
                page.keyboard.press("Escape")
                time.sleep(0.4)
            except Exception:
                break

    def _find_button(timeout):
        b = page.get_by_text("Export journal JSON file", exact=False).first
        b.wait_for(state="visible", timeout=timeout)
        return b

    # Primary: fresh deep-link load. Now that navigation waits for
    # domcontentloaded (not the full load event) this opens the dialog reliably.
    try:
        print("  [dialog] opening export via direct link...", flush=True)
        _dismiss_overlays()
        page.goto("about:blank")
        page.goto(EXPORT_URL, wait_until="domcontentloaded", timeout=60_000)
        b = _find_button(30_000)
        print("  [dialog] export dialog is open.", flush=True)
        return b
    except Exception as e:
        print(f"  [dialog] direct link failed ({e}); trying the menus...", flush=True)

    # Fallback: click through the menus (text-based -- 'Edit Journal' isn't a
    # plain button role). Escape first to clear a stray modal, and force=True so
    # the click punches through any leftover transparent overlay instead of
    # being intercepted by it.
    _dismiss_overlays()
    page.get_by_text("Edit Journal", exact=False).first.click(timeout=30_000, force=True)
    page.get_by_text("Journal Settings", exact=False).first.click(timeout=15_000, force=True)
    page.get_by_text("Export Journal", exact=False).last.click(timeout=15_000, force=True)
    return _find_button(30_000)


def _prune_old_exports(output_dir, keep_path):
    """
    Keep only the newest export in output_dir -- delete every OTHER *.zip so a
    pile of ~3 GB exports doesn't quietly eat the disk. Safe: older exports'
    content is already in the database; the zip is just a delivery file.
    Runs as the user's own process, so normal file deletion applies here.
    """
    try:
        keep = os.path.abspath(keep_path)
        for name in os.listdir(output_dir):
            if not name.lower().endswith(".zip"):
                continue
            full = os.path.abspath(os.path.join(output_dir, name))
            if full == keep:
                continue
            try:
                os.remove(full)
                print(f"  cleaned up old export: {name}", flush=True)
            except Exception as ex:
                print(f"  (couldn't remove {name}: {ex})", flush=True)
    except Exception as ex:
        print(f"  (export cleanup skipped: {ex})", flush=True)


def _verify_export(zip_path, nightly):
    """
    Sanity-check a just-saved export so a silent breakage (Day One changing the
    format, a truncated download, an empty journal) becomes a visible heads-up
    instead of a stale app. Confirms the file is a real zip containing a
    Journal JSON with a plausible number of entries. On anything suspicious it
    prints a warning and, on a nightly (unattended) run, sends a desktop
    notification. Never raises -- a failed check must not fail the export.
    """
    import zipfile
    try:
        if not os.path.exists(zip_path) or os.path.getsize(zip_path) == 0:
            msg = "the export file is missing or empty."
            print(f"  [self-check] WARNING: {msg}", flush=True)
            if nightly:
                _notify("Journal export looks wrong", msg)
            return False
        with zipfile.ZipFile(zip_path) as z:
            names = z.namelist()
            jsons = [n for n in names if n.lower().endswith(".json")]
            if not jsons:
                msg = ("the export has no JSON file inside -- Day One may have "
                       "changed its export format.")
                print(f"  [self-check] WARNING: {msg}", flush=True)
                if nightly:
                    _notify("Journal export format changed?", msg)
                return False
            import json as _json
            data = _json.loads(z.read(jsons[0]))
            entries = data.get("entries", [])
            n = len(entries) if isinstance(entries, list) else 0
            if n == 0:
                msg = ("the export contains 0 entries -- something is off "
                       "(sync not finished, or a format change).")
                print(f"  [self-check] WARNING: {msg}", flush=True)
                if nightly:
                    _notify("Journal export had 0 entries", msg)
                return False
        print(f"  [self-check] OK: valid export with {n} entries.", flush=True)
        return True
    except zipfile.BadZipFile:
        msg = "the export isn't a valid zip (download may have been truncated)."
        print(f"  [self-check] WARNING: {msg}", flush=True)
        if nightly:
            _notify("Journal export looks corrupt", msg)
        return False
    except Exception as ex:
        print(f"  [self-check] couldn't verify the export ({ex}).", flush=True)
        return False


def run(headless=False, nightly=False):
    from playwright.sync_api import sync_playwright

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    os.makedirs(PROFILE_DIR, exist_ok=True)
    _ensure_no_download_prompt(PROFILE_DIR, OUTPUT_DIR)

    print(f"Export will be saved into : {OUTPUT_DIR}")
    print(f"Login remembered in       : {PROFILE_DIR}")
    print(f"Mode                      : {'nightly (headless)' if nightly else 'interactive'}\n")

    before_names = set(os.listdir(OUTPUT_DIR))

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            PROFILE_DIR,
            headless=headless,
            accept_downloads=True,
        )

        # --- Defuse the File System Access API --------------------------------
        # Day One's web export saves the .zip with window.showSaveFilePicker(),
        # which pops a NATIVE OS "Save As" dialog that no download setting can
        # suppress (it's not a download -- it's a direct file-save through the
        # OS). We run this in every page BEFORE Day One's code loads: it swaps
        # showSaveFilePicker for a shim whose writable stream collects the bytes
        # and re-emits them as a normal <a download> download. Playwright then
        # intercepts THAT (accept_downloads=True) and we save it to OUTPUT_DIR
        # -- no native dialog, no clicking.
        ctx.add_init_script("""
        (() => {
          const emit = (name, blob) => {
            try {
              const url = URL.createObjectURL(blob);
              const a = document.createElement('a');
              a.href = url; a.download = name || 'dayone_export.zip';
              document.body.appendChild(a); a.click();
              // Do NOT revoke the blob URL here: a large export (hundreds of MB)
              // takes many seconds to be read to disk, and revoking early
              // truncates the saved file to 0 bytes. The page is torn down when
              // the export finishes, which frees it anyway.
              setTimeout(() => { try { a.remove(); } catch (e) {} }, 60000);
            } catch (e) { console.error('fs-shim emit failed', e); }
          };
          const makeHandle = (name) => ({
            kind: 'file',
            name: name || 'dayone_export.zip',
            createWritable: async () => {
              const chunks = [];
              return {
                write: async (chunk) => {
                  if (chunk && chunk.type && 'data' in chunk) chunk = chunk.data;
                  if (chunk != null) chunks.push(chunk);
                },
                seek: async () => {},
                truncate: async () => {},
                close: async () => { emit(name, new Blob(chunks)); },
                abort: async () => {},
              };
            },
            getFile: async () => new File([], name || 'dayone_export.zip'),
          });
          if (window.showSaveFilePicker) {
            window.showSaveFilePicker = async (opts) => {
              let name = 'dayone_export.zip';
              try {
                if (opts && opts.suggestedName) name = opts.suggestedName;
              } catch (e) {}
              return makeHandle(name);
            };
          }
        })();
        """)

        captured = {}

        def _grab(dl):
            try:
                print(f"  [download] intercepted: {dl.suggested_filename!r} "
                      f"from {dl.url[:80]}", flush=True)
            except Exception:
                print("  [download] intercepted (details unavailable)", flush=True)
            captured.setdefault("dl", dl)

        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.on("download", _grab)

            def _on_new_page(pg):
                try:
                    print(f"  [popup] Day One opened a new tab/window: {pg.url[:80]}", flush=True)
                except Exception:
                    pass
                pg.on("download", _grab)

            ctx.on("page", _on_new_page)

            # Downloads are handled entirely by Playwright (accept_downloads=True):
            # the FS-Access shim above turns Day One's file-save into a normal
            # blob download, Playwright intercepts it, and we save_as() it to
            # OUTPUT_DIR below. We deliberately do NOT also set a browser-level
            # download path here -- doing so made the browser write a second,
            # competing copy into OUTPUT_DIR that collided with the Playwright
            # save and left a 0-byte file.

            # --- Step 1: load the journal, forcing login if needed -----------
            print("Opening your journal...")
            page.goto(JOURNAL_URL, wait_until="domcontentloaded", timeout=60_000)

            ready_signal = page.get_by_text("Edit Journal", exact=False).first
            if nightly:
                # No one's watching -- if we're not already logged in and loaded
                # within a short window, the session has expired. Bail out and
                # tell the user to do one interactive login.
                try:
                    ready_signal.wait_for(state="visible",
                                          timeout=NIGHTLY_READY_WAIT_SECONDS * 1000)
                except Exception:
                    raise RuntimeError("SESSION_EXPIRED")
            else:
                print("Waiting until you're logged in and the journal has loaded")
                print("  (log in in the window if Day One asks)...")
                ready_signal.wait_for(state="visible",
                                      timeout=LOGIN_WAIT_SECONDS * 1000)
            print("Journal loaded.")

            # --- Step 1b: wait for the sync to FULLY settle ------------------
            # This is what makes a single run produce a COMPLETE export with no
            # manual warm-up: we watch the entries/media counts until they stop
            # climbing, rather than trusting the export button (which turns on
            # before sync is done).
            _wait_for_sync_complete(
                page,
                quiet_seconds=(45 if nightly else 60),
                max_seconds=(1800 if nightly else 3600),
            )

            # --- Step 2: open the export dialog (robustly) ------------------
            print("Opening the export dialog...")
            button = _open_export_dialog(page)

            # --- Step 3: wait for the export button to become clickable ----
            print("Waiting for the export button to become clickable...")
            wait_start = time.time()
            last_reopen = wait_start
            ceiling = 1800 if nightly else 3600
            while True:
                try:
                    if button.is_enabled():
                        break
                except Exception:
                    pass
                if time.time() - wait_start > ceiling:
                    raise RuntimeError(
                        "The export button stayed disabled too long -- the journal "
                        "may still be syncing. Running again resumes where it left off."
                    )
                if time.time() - last_reopen > 60:
                    try:
                        button = _open_export_dialog(page)
                    except Exception:
                        pass
                    last_reopen = time.time()
                print(f"  waiting for export to be ready... ({int(time.time() - wait_start)}s)", flush=True)
                time.sleep(10)

            print("Clicking export...")
            try:
                button.click()
            except Exception:
                button.click(force=True)

            # After clicking export, Day One walks through several dialogs before
            # the export file actually downloads:
            #   "Include Media"    -> "Include media"    (vs "Export without media")
            #   "Download Media?"  -> "Download now"     (fetches missing media; SLOW)
            #   "Downloading..."   -> "Close"            (when the fetch finishes)
            #   "Download Media?"  -> "Continue export"  (now that media is local)
            # ONE patient loop drives all of it: it clicks whichever of those
            # buttons is on screen, shows the real "X of Y GB" media progress and
            # the export-file MB, and finishes when the file lands. The first run
            # downloads ALL media (can be gigabytes / many minutes); after that
            # it's cached, so later runs breeze through.
            print("Including media and exporting -- the FIRST run downloads all your")
            print("media (can be gigabytes and take many minutes); progress below:")
            MEDIA_BUTTONS = ("Include media", "Download now", "Close", "Continue export")
            start = time.time()
            deadline = start + 5400  # 90-minute ceiling for a big first pull
            dest = None
            last_note = 0.0
            while time.time() < deadline:
                # 1) finished export file in the folder?
                found = _wait_for_new_export(OUTPUT_DIR, before_names, timeout_s=1)
                if found:
                    dest = found
                    break
                # 2) Playwright caught the download object?
                if "dl" in captured:
                    dl = captured["dl"]
                    dest = os.path.join(OUTPUT_DIR, dl.suggested_filename or "dayone_export.zip")
                    print("  download captured; saving...", flush=True)
                    dl.save_as(dest)
                    break
                # 3) click whichever media-flow button is showing.
                for label in MEDIA_BUTTONS:
                    try:
                        page.get_by_role("button", name=label, exact=False).first.click(timeout=1000)
                        print(f"  clicked '{label}'", flush=True)
                        time.sleep(2)
                        break
                    except Exception:
                        continue
                # 4) progress line every ~6s: media pull, file download, or working.
                if time.time() - last_note >= 6:
                    note = None
                    try:
                        m = re.search(r'Downloaded\s+([\d.]+\s*[KMGT]?B)\s+of\s+([\d.]+\s*[KMGT]?B)',
                                      page.inner_text("body"))
                        if m:
                            note = f"  downloading media into Day One: {m.group(1)} of {m.group(2)}"
                    except Exception:
                        pass
                    if not note:
                        try:
                            for n in os.listdir(OUTPUT_DIR):
                                if n.endswith(".crdownload") and n not in before_names:
                                    mb = os.path.getsize(os.path.join(OUTPUT_DIR, n)) / (1024 * 1024)
                                    note = f"  downloading export file... {mb:.1f} MB"
                                    break
                        except Exception:
                            pass
                    print(note or f"  working... ({int(time.time() - start)}s)", flush=True)
                    last_note = time.time()
                time.sleep(2)

            if not dest:
                raise RuntimeError(
                    "Export didn't finish within the time limit. Media is cached now, "
                    "so running again should be much faster."
                )

            print(f"\nSaved: {dest}")
            _prune_old_exports(OUTPUT_DIR, dest)
            _verify_export(dest, nightly)
            print("Done. Your watcher should ingest it within about a minute.")
        finally:
            try:
                ctx.close()
            except Exception:
                pass


def warmup():
    """
    Open the journal in the saved profile and KEEP THE WINDOW OPEN until you
    press Enter -- so a big first-time sync can finish at its own pace. Usually
    unnecessary, since a normal run now waits for sync on its own.
    """
    from playwright.sync_api import sync_playwright

    os.makedirs(PROFILE_DIR, exist_ok=True)
    _ensure_no_download_prompt(PROFILE_DIR, OUTPUT_DIR)
    print(f"Login/sync remembered in: {PROFILE_DIR}\n")

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            PROFILE_DIR, headless=False, accept_downloads=True
        )
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            print("Opening your journal...")
            page.goto(JOURNAL_URL, wait_until="domcontentloaded", timeout=60_000)
            print("\n" + "=" * 64)
            print("Log into Day One if asked, then let the journal FULLY SYNC.")
            print("Watch the 'Media' count near the journal title -- when it")
            print("stops climbing, syncing is done.")
            print("=" * 64)
            input("\nWhen syncing looks finished, press Enter here to close... ")
            print("Great -- profile is synced. Run:  python dayone_web_export.py")
        finally:
            try:
                ctx.close()
            except Exception:
                pass


if __name__ == "__main__":
    import datetime
    import traceback

    mode = sys.argv[1].lower() if len(sys.argv) > 1 else "export"

    # Mirror everything to export.log with a timestamped header, so a failed
    # run leaves a complete record you can open or share -- no scrolling the
    # terminal or reproducing the error.
    _logf = open(LOG_PATH, "a", encoding="utf-8")
    _logf.write(f"\n===== {datetime.datetime.now():%Y-%m-%d %H:%M:%S}  mode={mode} =====\n")
    sys.stdout = _Tee(sys.__stdout__, _logf)
    sys.stderr = _Tee(sys.__stderr__, _logf)

    try:
        if mode in ("warmup", "sync"):
            warmup()
        elif mode == "nightly":
            # Normally NOT headless: Day One's web app (especially the media
            # download flow) is unreliable in a headless browser but rock-solid
            # in a real one, and the 3am run is unattended so a briefly-visible
            # window is harmless. To TEST fully-invisible mode, pass a second
            # arg:  python dayone_web_export.py nightly headless
            # If that produces a good export, you can wire it into the schedule;
            # if it hangs or fails, drop the "headless" arg and keep the visible
            # window (which you won't see at 3am anyway).
            run(headless=("headless" in sys.argv), nightly=True)
        else:
            run(headless=False, nightly=False)
    except ImportError:
        print(
            "Playwright isn't installed yet. Run these two commands, then try again:\n"
            "    pip install playwright\n"
            "    python -m playwright install chromium\n"
        )
        sys.exit(1)
    except Exception as e:
        msg = str(e)
        # Full traceback into the log file (kept out of the console so the
        # terminal stays readable), plus a plain-English line on screen.
        try:
            _logf.write("\n" + traceback.format_exc() + "\n")
            _logf.flush()
        except Exception:
            pass
        if msg == "SESSION_EXPIRED":
            _notify(
                "Day One auto-export needs a login",
                "Your Day One web session expired. Run "
                "'python dayone_web_export.py' once and log in.",
            )
            print("\nSession expired -- run a normal 'python dayone_web_export.py' "
                  "once to log in again.")
        else:
            if mode == "nightly":
                _notify("Day One auto-export failed", msg[:180])
            print(f"\nExport did not complete: {type(e).__name__}: {msg}")
        print(f"\nFull details were saved to: {LOG_PATH}")
        print("If you'd like help, just share that export.log file "
              "(or paste its last dozen lines).")
        sys.exit(1)
    finally:
        try:
            _logf.flush()
            _logf.close()
        except Exception:
            pass
