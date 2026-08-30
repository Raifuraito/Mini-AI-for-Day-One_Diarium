import os
import sys
import subprocess
import time

# Resolves to wherever THIS file lives -- so moving the whole journal-rag
# folder never requires touching this again.
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))

# sys.executable is always the Python that's currently running this script,
# so no hardcoded path needed -- works on any machine, any Python install.
PYTHON_EXE = sys.executable

CHROME_CHECK_SECONDS = 5     # how often to check whether Chrome is open
INGEST_CHECK_SECONDS = 60    # how often to check for new/changed journal exports

server_process = None
seconds_since_ingest_check = INGEST_CHECK_SECONDS  # run once immediately on startup


def is_chrome_running():
    """
    Checks whether Chrome is currently running. This is the one genuinely
    OS-specific piece of this file -- Windows and Mac don't share a
    process-listing command -- so only this helper branches by platform;
    everything else in the watcher loop is identical on both.
    """
    try:
        if sys.platform == "win32":
            result = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq chrome.exe"],
                capture_output=True, text=True,
            )
            return "chrome.exe" in result.stdout
        else:
            # Mac (and Linux, as a bonus) -- pgrep is standard on both.
            # -f matches against the full command line ("Google Chrome"
            # is the actual process name on Mac, with a space in it),
            # which is more forgiving than an exact-name match.
            result = subprocess.run(
                ["pgrep", "-f", "Google Chrome"],
                capture_output=True, text=True,
            )
            return result.returncode == 0
    except FileNotFoundError:
        # tasklist/pgrep genuinely missing (unusual) -- don't crash the
        # watcher loop over it, just assume "not running" this cycle and
        # try again next time.
        return False


print(f"Watcher running: keeps the server up whenever Chrome is open, and "
      f"checks for new journal exports every {INGEST_CHECK_SECONDS}s. "
      f"Leave this window open.")

while True:
    # --- Keep the server running whenever Chrome is open ---
    # Checks "is Chrome open AND the server not already running" every loop,
    # rather than only reacting to the moment Chrome transitions from closed
    # to open. That transition-only version missed the common case where
    # this script itself starts (e.g. via Task Scheduler at login) while
    # Chrome is already open -- it would then never launch the server until
    # Chrome was fully closed and reopened. This condition is self-correcting
    # instead: whatever order Chrome and this watcher start in, and even if
    # the server process dies for some reason, it gets (re)launched within
    # CHROME_CHECK_SECONDS as long as Chrome is open.
    chrome_running = is_chrome_running()
    server_running = server_process is not None and server_process.poll() is None

    if chrome_running and not server_running:
        server_process = subprocess.Popen(
            [PYTHON_EXE, "webapp/server.py"],
            cwd=PROJECT_DIR
        )

    # --- Pull new exports from the sync folder into local storage ---
    # If a cloud-sync drop zone is configured (JOURNAL_SYNC_DIR), any
    # .json or .zip files found there are copied into EXPORT_WATCH_DIR
    # (the local storage folder) and then deleted from the sync folder.
    # This keeps cloud storage clean -- exports are just a channel, not
    # a permanent home. The copy-then-delete order guarantees the file
    # is safely in local storage before the sync copy is removed.
    seconds_since_ingest_check += CHROME_CHECK_SECONDS
    if seconds_since_ingest_check >= INGEST_CHECK_SECONDS:
        seconds_since_ingest_check = 0

        # Lazy import so config.py's .env loading picks up any wizard changes
        import importlib, config as _cfg
        importlib.reload(_cfg)

        sync_dir = getattr(_cfg, "SYNC_WATCH_DIR", "") or ""
        export_dir = _cfg.EXPORT_WATCH_DIR

        if sync_dir and os.path.isdir(sync_dir):
            os.makedirs(export_dir, exist_ok=True)
            import shutil, glob as _glob
            for pattern in ("*.json", "*.zip"):
                for src_file in _glob.glob(os.path.join(sync_dir, pattern)):
                    dest = os.path.join(export_dir, os.path.basename(src_file))
                    try:
                        shutil.copy2(src_file, dest)
                        os.remove(src_file)
                        print(f"  Synced: {os.path.basename(src_file)} -> exports/")
                    except Exception as ex:
                        print(f"  Sync error for {os.path.basename(src_file)}: {ex}")

        # --- Run ingest on the local storage folder ---
        # ingest.py scans EXPORT_WATCH_DIR and cheaply skips any file
        # whose mtime hasn't changed since last run.
        subprocess.run([PYTHON_EXE, "ingest.py"], cwd=PROJECT_DIR)

    time.sleep(CHROME_CHECK_SECONDS)
