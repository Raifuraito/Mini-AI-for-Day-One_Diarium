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
    result = subprocess.run(["tasklist", "/FI", "IMAGENAME eq chrome.exe"],
                             capture_output=True, text=True)
    chrome_running = "chrome.exe" in result.stdout
    server_running = server_process is not None and server_process.poll() is None

    if chrome_running and not server_running:
        server_process = subprocess.Popen(
            [PYTHON_EXE, "webapp/server.py"],
            cwd=PROJECT_DIR
        )

    # --- Periodically check for new/changed journal exports ---
    # ingest.py (run with no path argument) scans EXPORT_WATCH_DIR itself
    # and cheaply skips any file whose mtime hasn't changed since last run,
    # so it's safe -- and much simpler than duplicating that change-detection
    # here -- to just call it on a timer. Exporting from Day One into that
    # folder is now the only manual step; this picks it up within a minute.
    seconds_since_ingest_check += CHROME_CHECK_SECONDS
    if seconds_since_ingest_check >= INGEST_CHECK_SECONDS:
        seconds_since_ingest_check = 0
        subprocess.run([PYTHON_EXE, "ingest.py"], cwd=PROJECT_DIR)

    time.sleep(CHROME_CHECK_SECONDS)
