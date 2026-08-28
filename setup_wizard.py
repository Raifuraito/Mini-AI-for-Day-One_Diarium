"""
setup_wizard.py

A local web form for configuring this app without editing any code, and
for installing whatever Python packages it needs -- run it once when you
first set the project up, or again anytime you want to change a path,
your API key, or install something you skipped the first time.

Run it:
    python setup_wizard.py

Then open http://localhost:5050 in your browser, fill in the required
fields, and save. It writes a local `.env` file that config.py reads
automatically the next time you run ingest.py, ask.py, tag_backfill.py,
or the server -- so this is the only place you ever need to type your
API key or set up folder paths.

This page only listens on 127.0.0.1 (your own computer) -- unlike the
main chat app, it is never reachable from your phone or any other
device, which matters since this is the one page that handles your raw
API key. Nothing you type here is sent anywhere except into that local
.env file. See SECURITY.md for more on this.

--- A note on the very first line of actual code below ---

Below the standard library imports, there's a check for whether Flask
itself is installed, BEFORE the `from flask import ...` line that this
whole file depends on. That's deliberate: if this is the very first time
you're running anything in this project, you may not have installed
Flask yet, and a bare `ModuleNotFoundError` with a stack trace is a
genuinely bad first thing for a page promising to be the easy path to
see. So this offers to install it for you right there in the terminal,
using only Python's own standard library (no dependency on anything this
script itself might be missing) -- then re-imports and carries on.
"""

import os
import subprocess
import sys
import threading
import time

# --- Self-bootstrap: make sure Flask is available before we need it ---
# Deliberately written using ONLY the standard library (os, subprocess,
# sys -- all always present in any normal Python install) so that this
# check itself can never fail with the same kind of missing-dependency
# error it exists to catch.
try:
    import flask  # noqa: F401  (just checking it's importable)
except ImportError:
    print(
        "\nThis needs one small package (Flask) that isn't installed yet.\n"
        "Installing it now -- this only happens once...\n"
    )
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "flask"])
        print("\nFlask installed. Continuing setup...\n")
    except subprocess.CalledProcessError:
        print(
            "\nCouldn't install Flask automatically. Please run this yourself,\n"
            "then try `python setup_wizard.py` again:\n\n"
            f"    {sys.executable} -m pip install flask\n"
        )
        sys.exit(1)

from flask import Flask, jsonify, request  # noqa: E402  (import after the bootstrap check above, on purpose)

app = Flask(__name__)

_PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(_PROJECT_ROOT, ".env")

# Every path field the wizard can configure. "required" fields are shown
# up front; everything else lives behind the "Advanced" disclosure, since
# their defaults are almost always fine and the user shouldn't have to
# look at (or understand) them just to get started. This list is also
# what write_env()/read_existing_env() use to know which keys are paths
# (vs. the API key, which is handled separately since it's a password
# field with different validation).
PATH_FIELDS = [
    {
        "env_key": "JOURNAL_EXPORT_DIR",
        "label": "Journal export folder",
        "hint": "Where your Day One (or Diarium) export file(s) land.",
        "default": "./exports",
        "required": True,
    },
    {
        "env_key": "JOURNAL_DB_DIR",
        "label": "Database folder",
        "hint": "Where the local vector database is stored. Advanced -- the default is almost always right.",
        "default": "./chroma_db",
        "required": False,
    },
    {
        "env_key": "JOURNAL_PROCESSED_LOG",
        "label": "Processed-entries tracking file",
        "hint": "Tracks which entries are already ingested, so re-runs don't re-process everything. Advanced.",
        "default": "./processed_entries.json",
        "required": False,
    },
    {
        "env_key": "JOURNAL_TAG_LOG",
        "label": "Tag-backfill tracking file",
        "hint": "Tracks which entries tag_backfill.py has already attempted. Advanced.",
        "default": "./tag_backfill_log.json",
        "required": False,
    },
]

# Packages this project needs. Split into "core" (required for the app to
# run at all) and "photo search" (a genuinely optional, much heavier
# install -- open-clip-torch pulls in PyTorch, which is a large download).
# Both installs are entirely skippable from the page; nothing here ever
# runs without the user clicking the corresponding button themselves.
CORE_PACKAGES = ["chromadb", "anthropic", "flask"]
PHOTO_SEARCH_PACKAGES = ["open-clip-torch", "pillow"]

# Tracks the most recent package-install run so the page can poll for
# progress. A real production app might use something fancier than a
# plain module-level dict, but this only ever has one user (you, in one
# browser tab, on your own computer) so there's nothing to gain from more
# machinery than that.
_install_state = {
    "running": False,
    "log": [],
    "done": False,
    "success": None,
}
_install_lock = threading.Lock()


def read_existing_env():
    """Returns whatever's already in .env, so the form is pre-filled with
    current values on a second visit instead of showing blank fields and
    silently losing anything you already set."""
    values = {}
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def write_env(values):
    lines = [
        "# Written by setup_wizard.py -- feel free to hand-edit, or just",
        "# re-run the wizard. This file is already excluded from git.",
        "",
    ]
    for key, value in values.items():
        lines.append(f'{key}="{value}"')
    with open(ENV_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def _is_safe_relative_path(path):
    """
    Guards the "create this folder" button against creating something
    outside the project, or somewhere surprising. Rejects absolute paths
    entirely (Windows drive letters like C:\\ included) and anything that
    tries to climb out of the project folder with "..". A plain relative
    path like "./exports" or "my-exports" is what this is meant for --
    matching every default value in PATH_FIELDS above.
    """
    if not path or not path.strip():
        return False
    path = path.strip()
    if os.path.isabs(path):
        return False
    if ":" in path:  # catches "C:\..." even though os.path.isabs is Windows-build-dependent
        return False
    normalized = os.path.normpath(path)
    if normalized.startswith("..") or normalized == "..":
        return False
    return True


def render_page(banner_html, api_key, path_values, tagging_enabled=False):
    page = _PAGE_TEMPLATE
    page = page.replace("__BANNER__", banner_html)
    page = page.replace("__API_KEY__", api_key)
    page = page.replace("__TAGGING_CHECKED__", "checked" if tagging_enabled else "")

    required_fields_html = []
    advanced_fields_html = []
    for field in PATH_FIELDS:
        value = path_values.get(field["env_key"], field["default"])
        field_html = f"""
    <label>{field["label"]}
      <div class="hint">{field["hint"]}</div>
    </label>
    <div class="path-row">
      <input type="text" name="{field["env_key"]}" value="{value}" placeholder="{field["default"]}" id="field-{field["env_key"]}">
      <button type="button" class="create-btn" onclick="createFolder('{field["env_key"]}')">Create this folder</button>
    </div>
    <div class="folder-status" id="status-{field["env_key"]}"></div>
"""
        if field["required"]:
            required_fields_html.append(field_html)
        else:
            advanced_fields_html.append(field_html)

    page = page.replace("__REQUIRED_PATH_FIELDS__", "".join(required_fields_html))
    page = page.replace("__ADVANCED_PATH_FIELDS__", "".join(advanced_fields_html))
    return page


_PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Journal RAG Setup</title>
<style>
  body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    background: #FAF6EE;
    color: #232C3D;
    max-width: 560px;
    margin: 40px auto;
    padding: 0 20px 60px;
    line-height: 1.5;
  }
  h1 { font-size: 22px; }
  h2 { font-size: 16px; margin-top: 36px; border-top: 1px solid #E8E1D2; padding-top: 20px; }
  p.sub { color: #4A5568; font-size: 14px; margin-top: -8px; }
  label { display: block; font-weight: 600; margin-top: 20px; font-size: 14px; }
  .hint { font-weight: 400; color: #8B8478; font-size: 12.5px; margin-top: 2px; }
  input[type=text], input[type=password] {
    width: 100%;
    box-sizing: border-box;
    padding: 10px 12px;
    margin-top: 6px;
    border: 1px solid #E8E1D2;
    border-radius: 6px;
    font-size: 14px;
  }
  .path-row { display: flex; gap: 8px; align-items: flex-start; }
  .path-row input { flex: 1; }
  .create-btn, .install-btn {
    margin-top: 6px;
    background: #E8E1D2;
    color: #232C3D;
    border: none;
    padding: 10px 14px;
    border-radius: 6px;
    font-size: 13px;
    cursor: pointer;
    white-space: nowrap;
  }
  .create-btn:hover, .install-btn:hover { background: #DCD4C2; }
  .create-btn:disabled, .install-btn:disabled { opacity: 0.6; cursor: default; }
  .folder-status { font-size: 12.5px; margin-top: 4px; min-height: 16px; }
  .folder-status.ok { color: #4A6741; }
  .folder-status.err { color: #A14E3C; }
  button[type=submit] {
    margin-top: 26px;
    background: #5E7052;
    color: white;
    border: none;
    padding: 11px 22px;
    border-radius: 6px;
    font-size: 15px;
    cursor: pointer;
  }
  button[type=submit]:hover { background: #7C9070; }
  .banner {
    background: #E8E1D2;
    padding: 12px 16px;
    border-radius: 6px;
    font-size: 14px;
    margin-bottom: 10px;
  }
  .success { background: #DCE8D4; }
  .warn { background: #E8CFC4; }
  code { background: #E8E1D2; padding: 1px 5px; border-radius: 3px; }
  details { margin-top: 20px; }
  summary {
    cursor: pointer;
    font-weight: 600;
    font-size: 14px;
    padding: 10px 0;
  }
  .privacy-note {
    background: #EDF1E8;
    border: 1px solid #D3DFC9;
    border-radius: 6px;
    padding: 12px 16px;
    font-size: 13px;
    margin-top: 20px;
    color: #3F4F38;
  }
  .install-section {
    background: #F3EFE3;
    border-radius: 8px;
    padding: 16px;
    margin-top: 14px;
  }
  .install-section p { font-size: 13px; margin-top: 4px; color: #4A5568; }
  .tagging-note {
    background: #F3EFE3;
    border-radius: 8px;
    padding: 16px 18px;
    margin-top: 10px;
  }
  .tagging-note p {
    font-size: 13px;
    line-height: 1.5;
    color: #4A5568;
    margin: 0 0 10px;
  }
  .tagging-note .checkbox-label {
    margin-top: 4px;
    font-weight: 600;
    color: #232C3D;
  }
  #install-log, #photo-install-log {
    background: #232C3D;
    color: #D4E8CB;
    font-family: 'SF Mono', Consolas, monospace;
    font-size: 12px;
    padding: 10px;
    border-radius: 6px;
    margin-top: 10px;
    max-height: 160px;
    overflow-y: auto;
    white-space: pre-wrap;
    display: none;
  }
  label.checkbox-label {
    font-weight: 400;
    display: flex;
    align-items: center;
    gap: 8px;
    margin-top: 12px;
  }
  label.checkbox-label input { margin: 0; width: auto; }
</style>
</head>
<body>
  <h1>Set up your journal RAG app</h1>
  <p class="sub">Saved to a local <code>.env</code> file in this folder -- nothing leaves your computer.</p>
  __BANNER__

  <div class="install-section">
    <strong>Step 1 (optional): Install required packages</strong>
    <p>Installs <code>chromadb</code> and <code>anthropic</code> -- the two packages the app needs to run.
       Skip this if you've already installed them yourself, or plan to.</p>
    <button type="button" class="install-btn" id="core-install-btn" onclick="startInstall('core')">Install packages</button>
    <div id="install-log"></div>

    <label class="checkbox-label" style="margin-top:18px;">
      <input type="checkbox" id="photo-search-toggle" onchange="togglePhotoSearch()">
      Also set up photo search (finds photos by what's <em>in</em> them, not just filenames)
    </label>
    <p id="photo-search-note" style="display:none;">
      This installs <code>open-clip-torch</code> and <code>pillow</code> -- a much larger download
      (PyTorch alone is several hundred MB) since it includes an image-recognition model.
      Fully optional; the app works fine without it, just without photo search.
    </p>
    <button type="button" class="install-btn" id="photo-install-btn" style="display:none;"
            onclick="startInstall('photo')">Install photo search packages</button>
    <div id="photo-install-log"></div>
  </div>

  <form method="POST" action="/save">
    <h2>Step 2: Your API key</h2>
    <label>Anthropic API key
      <div class="hint">From console.anthropic.com &rarr; Settings &rarr; API Keys. Required.</div>
    </label>
    <input type="password" name="ANTHROPIC_API_KEY" value="__API_KEY__" placeholder="sk-ant-..." required>

    <h2>Step 3: Tagging &amp; Max Recall</h2>
    <div class="tagging-note">
      <p>
        Tagging reads each entry once to note the people, places, and themes in it &mdash;
        this is what lets <strong>Max Recall</strong> pull up <em>every</em> entry about a topic,
        not just the closest few matches. It costs a small amount per entry
        (roughly a fraction of a cent each; see the README for real numbers), and on
        a first-time ingest of a large journal that can add up before you've decided
        you want it &mdash; so it's off by default.
      </p>
      <p>
        Leaving this off doesn't break anything: asking questions, embeddings, and
        the mood chart all work exactly the same either way. You just won't have
        Max Recall's completeness guarantee until this is on. You can turn it on
        (or back off) any time by coming back to this page &mdash; nothing about this
        choice is permanent.
      </p>
      <label class="checkbox-label">
        <input type="checkbox" name="ENABLE_TAGGING" __TAGGING_CHECKED__>
        Enable tagging (turns on Max Recall, small ongoing cost per new entry)
      </label>
    </div>

    <h2>Step 4: Folders</h2>
    __REQUIRED_PATH_FIELDS__

    <details>
      <summary>Advanced (optional -- the defaults are almost always fine)</summary>
      __ADVANCED_PATH_FIELDS__
    </details>

    <div class="privacy-note">
      <strong>About your privacy:</strong> everything above is saved into a plain text file
      (<code>.env</code>) that lives only in this project folder, on this computer. It is never
      uploaded anywhere, never sent to Anthropic except as part of the specific journal excerpts
      you ask questions about, and is already excluded from this project's git repository so it
      can never end up on GitHub by accident. No one else -- including us -- can see it.
    </div>

    <button type="submit">Save</button>
  </form>

<script>
function createFolder(envKey) {
  const input = document.getElementById('field-' + envKey);
  const status = document.getElementById('status-' + envKey);
  const btn = event.target;
  btn.disabled = true;
  status.className = 'folder-status';
  status.textContent = 'Creating...';
  fetch('/create-folder', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({path: input.value})
  })
    .then(r => r.json())
    .then(data => {
      btn.disabled = false;
      if (data.ok) {
        status.className = 'folder-status ok';
        status.textContent = data.already_existed
          ? 'Already exists -- nothing to do.'
          : 'Created: ' + data.full_path;
      } else {
        status.className = 'folder-status err';
        status.textContent = data.error;
      }
    })
    .catch(() => {
      btn.disabled = false;
      status.className = 'folder-status err';
      status.textContent = 'Something went wrong -- check the terminal window for details.';
    });
}

function togglePhotoSearch() {
  const checked = document.getElementById('photo-search-toggle').checked;
  document.getElementById('photo-search-note').style.display = checked ? 'block' : 'none';
  document.getElementById('photo-install-btn').style.display = checked ? 'inline-block' : 'none';
}

function startInstall(which) {
  const btnId = which === 'core' ? 'core-install-btn' : 'photo-install-btn';
  const logId = which === 'core' ? 'install-log' : 'photo-install-log';
  const btn = document.getElementById(btnId);
  const log = document.getElementById(logId);
  btn.disabled = true;
  btn.textContent = 'Installing...';
  log.style.display = 'block';
  log.textContent = 'Starting install -- this can take a minute or two' +
    (which === 'photo' ? ' (longer for photo search -- it includes a large download)' : '') + '...\\n';

  fetch('/install/' + which, {method: 'POST'})
    .then(() => pollInstallStatus(which, btn, log));
}

function pollInstallStatus(which, btn, log) {
  fetch('/install-status/' + which)
    .then(r => r.json())
    .then(data => {
      log.textContent = data.log.join('\\n');
      log.scrollTop = log.scrollHeight;
      if (data.done) {
        btn.disabled = false;
        btn.textContent = data.success ? 'Done \u2713' : 'Failed -- click to retry';
      } else {
        setTimeout(() => pollInstallStatus(which, btn, log), 1000);
      }
    })
    .catch(() => {
      // A transient fetch error while polling isn't fatal -- the install
      // itself keeps running server-side either way. Just try again.
      setTimeout(() => pollInstallStatus(which, btn, log), 1500);
    });
}
</script>
</body>
</html>
"""


def _tagging_enabled_from(values):
    """Reads the ENABLE_TAGGING value out of a values dict the same way
    config.py's own parsing does (case-insensitive "true", tolerant of
    whitespace) -- kept in sync with that expression deliberately, since
    this is just for pre-filling the checkbox's checked state, not the
    actual gate that decides whether ingest.py calls the API."""
    return (values.get("ENABLE_TAGGING") or "").strip().lower() == "true"


@app.route("/", methods=["GET"])
def index():
    existing = read_existing_env()
    banner = ""
    if existing.get("ANTHROPIC_API_KEY"):
        banner = '<div class="banner">Already configured. Change anything below and save again to update it.</div>'
    return render_page(
        banner,
        existing.get("ANTHROPIC_API_KEY", ""),
        existing,
        tagging_enabled=_tagging_enabled_from(existing),
    )


@app.route("/save", methods=["POST"])
def save():
    api_key = (request.form.get("ANTHROPIC_API_KEY") or "").strip()

    path_values = {}
    for field in PATH_FIELDS:
        raw = (request.form.get(field["env_key"]) or "").strip()
        path_values[field["env_key"]] = raw or field["default"]

    # A checkbox that's unchecked sends NOTHING in the POST body at all --
    # its key just won't be present in request.form. So "was this key
    # submitted" (not "what value did it have") is what actually tells us
    # whether the box was checked, which is why this doesn't follow the
    # same (request.form.get(...) or default) pattern as the fields above.
    tagging_enabled = "ENABLE_TAGGING" in request.form

    if len(api_key) < 20:
        banner = ('<div class="banner warn">That doesn&#39;t look like a full API key -- '
                   'please paste the whole thing from console.anthropic.com.</div>')
        return render_page(banner, "", path_values, tagging_enabled=tagging_enabled)

    values = read_existing_env()
    values["ANTHROPIC_API_KEY"] = api_key
    values.update(path_values)
    values["ENABLE_TAGGING"] = "true" if tagging_enabled else "false"
    write_env(values)

    tagging_note = (
        "Tagging is on -- new entries will be tagged automatically at ingest time."
        if tagging_enabled else
        "Tagging is off -- ingest.py will skip tag extraction (no related API "
        "calls) until you turn it back on here."
    )
    banner = ('<div class="banner success">Saved! ' + tagging_note + ' Next: close this tab, '
               'then in your terminal run <code>python ingest.py</code>, and once that '
               'finishes, <code>python webapp/server.py</code>.</div>')
    return render_page(banner, api_key, path_values, tagging_enabled=tagging_enabled)


@app.route("/create-folder", methods=["POST"])
def create_folder():
    """
    Creates a folder relative to the project root. Deliberately restricted
    to relative paths inside (or alongside) the project -- see
    _is_safe_relative_path -- so this button can never be used to create
    or touch anything outside this project folder, no matter what gets
    typed into the box.
    """
    data = request.get_json(silent=True) or {}
    raw_path = (data.get("path") or "").strip()

    if not _is_safe_relative_path(raw_path):
        return jsonify({
            "ok": False,
            "error": "Please use a simple relative folder name (like ./exports), not a full path.",
        })

    full_path = os.path.join(_PROJECT_ROOT, raw_path)
    already_existed = os.path.isdir(full_path)
    try:
        os.makedirs(full_path, exist_ok=True)
    except OSError as e:
        return jsonify({"ok": False, "error": f"Couldn't create that folder: {e}"})

    return jsonify({"ok": True, "full_path": full_path, "already_existed": already_existed})


def _run_install(which, packages):
    """
    Runs in a background thread so the page stays responsive while pip
    does its (sometimes slow) thing, and so the polling endpoint below
    has something to report progress from. Uses sys.executable -m pip
    (never a bare `pip` or `python` command) specifically because that's
    the one form that's guaranteed to install into the same Python
    environment this script itself is running under, on both Windows and
    Mac -- a bare `pip` can silently target a different Python install
    than the one running this file, which is a common source of "I
    installed it but it still says missing" confusion.
    """
    with _install_lock:
        _install_state["running"] = True
        _install_state["done"] = False
        _install_state["success"] = None
        _install_state["log"] = [f"Installing: {', '.join(packages)}"]

    try:
        process = subprocess.Popen(
            [sys.executable, "-m", "pip", "install"] + packages,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        for line in process.stdout:
            with _install_lock:
                _install_state["log"].append(line.rstrip())
        process.wait()
        success = process.returncode == 0
    except Exception as e:
        with _install_lock:
            _install_state["log"].append(f"Install failed to start: {e}")
        success = False

    with _install_lock:
        _install_state["running"] = False
        _install_state["done"] = True
        _install_state["success"] = success
        _install_state["log"].append(
            "Done." if success else "Install failed -- see the log above, or run it "
            "yourself in a terminal: " + " ".join([sys.executable, "-m", "pip", "install"] + packages)
        )


@app.route("/install/<which>", methods=["POST"])
def install(which):
    if which not in ("core", "photo"):
        return jsonify({"ok": False, "error": "Unknown install target."}), 400

    with _install_lock:
        if _install_state["running"]:
            return jsonify({"ok": False, "error": "An install is already running."}), 409

    packages = CORE_PACKAGES if which == "core" else PHOTO_SEARCH_PACKAGES
    thread = threading.Thread(target=_run_install, args=(which, packages), daemon=True)
    thread.start()
    # Give the thread a beat to flip "running" to True before the first
    # poll, so the very first status check doesn't race a still-starting
    # thread and report "done" from stale leftover state.
    time.sleep(0.1)
    return jsonify({"ok": True})


@app.route("/install-status/<which>", methods=["GET"])
def install_status(which):
    with _install_lock:
        return jsonify({
            "running": _install_state["running"],
            "done": _install_state["done"],
            "success": _install_state["success"],
            "log": list(_install_state["log"]),
        })


def _open_browser_shortly():
    """
    Opens the setup page automatically a moment after the server starts,
    so double-clicking a launcher script is genuinely a one-click action
    -- no "now go type this address into your browser" step in between.
    Runs in a background thread with a short delay because app.run() below
    blocks the main thread for as long as the server is up; without the
    delay, this could try to open the page before Flask is actually
    listening yet on some slower machines.

    If this fails for any reason (an unusual system with no default
    browser configured, a headless environment, etc.) it fails silently
    -- the terminal message printed either way is still a completely
    valid fallback, so a browser that doesn't auto-open is a minor
    inconvenience here, never a broken setup.
    """
    import webbrowser
    time.sleep(1.2)
    try:
        webbrowser.open("http://localhost:5050")
    except Exception:
        pass


if __name__ == "__main__":
    print("\nOpen this in your browser to finish setup: http://localhost:5050")
    print("(Trying to open it for you automatically now...)\n")
    threading.Thread(target=_open_browser_shortly, daemon=True).start()
    app.run(host="127.0.0.1", port=5050, debug=False)
