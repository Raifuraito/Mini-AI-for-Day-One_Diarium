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

# Set by webapp/server.py's /open-settings route before it spawns this
# process, so /save knows it's safe to close itself automatically when
# you're done -- a normal "python setup_wizard.py" launch never sets this,
# and behaves exactly as before (stays open after Save).
_EMBEDDED_LAUNCH = os.environ.get("JOURNAL_WIZARD_EMBEDDED") == "1"

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
        "env_key": "JOURNAL_SYNC_DIR",
        "label": "Sync folder (drop zone)",
        "hint": (
            "A cloud-synced folder where you'll drop journal exports from "
            "your phone or other devices. New files here are automatically "
            "copied into the storage folder below and then deleted from here, "
            "so they don't eat your cloud storage quota. Pick a provider "
            "below, or Browse to any folder."
        ),
        "default": "",
        "required": True,
    },
    {
        "env_key": "JOURNAL_EXPORT_DIR",
        "label": "Storage folder (local)",
        "hint": (
            "Where journal exports are kept permanently on this computer. "
            "The watcher copies files here from the sync folder above, then "
            "runs ingest. The default is fine for most setups."
        ),
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


# ---------------------------------------------------------------------------
# Cloud provider definitions -- always shown, regardless of detection
# ---------------------------------------------------------------------------

# Each provider has: label, a simple inline SVG icon, and default folder
# paths to check (first match wins when filling the path field). The SVGs
# are kept small and monochrome-friendly so they render well at 28x28 in
# the wizard's grid. "Other" has no default paths -- it just opens the
# native folder picker.
CLOUD_PROVIDERS = [
    {
        "id": "dropbox",
        "label": "Dropbox",
        "color": "#0061FF",
        "icon": '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M6 2l6 3.75L6 9.5 0 5.75zm12 0l6 3.75-6 3.75-6-3.75zM0 13.25L6 9.5l6 3.75L6 17zM18 9.5l6 3.75L18 17l-6-3.75zM6 18.25l6-3.75 6 3.75L12 22z"/></svg>',
        "paths_win": ["{home}\\Dropbox"],
        "paths_mac": ["{home}/Dropbox"],
    },
    {
        "id": "icloud",
        "label": "iCloud Drive",
        "color": "#3693F3",
        "icon": '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M19.35 10.04A7.49 7.49 0 0012 4C9.11 4 6.6 5.64 5.35 8.04A5.994 5.994 0 000 14c0 3.31 2.69 6 6 6h13c2.76 0 5-2.24 5-5 0-2.64-2.05-4.78-4.65-4.96z"/></svg>',
        "paths_win": ["{home}\\iCloudDrive"],
        "paths_mac": [
            "{home}/Library/Mobile Documents/com~apple~CloudDocs",
            "{home}/iCloudDrive",
        ],
    },
    {
        "id": "onedrive",
        "label": "OneDrive",
        "color": "#0078D4",
        "icon": '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M19.35 10.04A7.49 7.49 0 0012 4c-2.89 0-5.4 1.64-6.65 4.04A5.994 5.994 0 000 14c0 3.31 2.69 6 6 6h13c2.76 0 5-2.24 5-5 0-2.64-2.05-4.78-4.65-4.96z"/></svg>',
        "paths_win": ["{home}\\OneDrive"],
        "paths_mac": ["{home}/OneDrive"],
    },
    {
        "id": "gdrive",
        "label": "Google Drive",
        "color": "#4285F4",
        "icon": '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M7.71 3.5L1.15 15l3.43 5.93L11.14 9.43zM12.29 3.5h6.57L12.29 15H5.71zM15.57 9.43l6.57 11.5H8.71l3.43-5.93z" opacity=".9"/></svg>',
        "paths_win": [
            "{home}\\Google Drive",
            "{home}\\Google Drive\\My Drive",
        ],
        "paths_mac": [
            "{home}/Google Drive",
            "{home}/Google Drive/My Drive",
        ],
    },
    {
        "id": "proton",
        "label": "Proton Drive",
        "color": "#6D4AFF",
        "icon": '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M3 4.5A1.5 1.5 0 014.5 3h15A1.5 1.5 0 0121 4.5v15a1.5 1.5 0 01-1.5 1.5h-15A1.5 1.5 0 013 19.5v-15zm3 3v9h3v-3.5h3.5a2.75 2.75 0 000-5.5H6z"/></svg>',
        "paths_win": ["{home}\\Proton Drive"],
        "paths_mac": ["{home}/Proton Drive"],
    },
    {
        "id": "mega",
        "label": "MEGA",
        "color": "#D9272E",
        "icon": '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M3 5v14h4V9.5l3 5h4l3-5V19h4V5h-5l-4 7-4-7H3z"/></svg>',
        "paths_win": ["{home}\\MEGA"],
        "paths_mac": ["{home}/MEGA"],
    },
    {
        "id": "other",
        "label": "Other",
        "color": "#6B7280",
        "icon": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 7a2 2 0 012-2h4l2 2h8a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2V7z" stroke-linejoin="round"/><path d="M12 11v4m-2-2h4" stroke-linecap="round"/></svg>',
        "paths_win": [],
        "paths_mac": [],
    },
]


def _resolve_cloud_paths():
    """Return provider info with default paths filled in (no filesystem scanning)."""
    home = os.path.expanduser("~")
    is_win = sys.platform == "win32"
    results = []
    for prov in CLOUD_PROVIDERS:
        paths = prov["paths_win"] if is_win else prov["paths_mac"]
        resolved = [p.replace("{home}", home) for p in paths]
        results.append({
            "id": prov["id"],
            "label": prov["label"],
            "color": prov["color"],
            "icon": prov["icon"],
            "default_path": resolved[0] if resolved else "",
        })
    return results


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


import json as _json
import html as _html


def _register_autostart():
    """
    Registers watcher.py to start automatically at login, so nothing needs
    a terminal window left open and nothing needs manual Task Scheduler /
    launchd setup.

    Returns (ok: bool, message: str) -- message is shown directly in the
    wizard's success banner either way, so a failure here is something the
    user can actually see and act on rather than a silent no-op.
    """
    python_exe = sys.executable
    watcher_path = os.path.join(_PROJECT_ROOT, "watcher.py")

    if sys.platform == "win32":
        task_name = "MiniAI for DayOne & Diarium Watcher"
        tr_value = f'"{python_exe}" "{watcher_path}"'
        try:
            result = subprocess.run(
                ["schtasks", "/Create", "/TN", task_name, "/TR", tr_value,
                 "/SC", "ONLOGON", "/RL", "HIGHEST", "/F"],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode == 0:
                return True, (
                    f'Windows: registered "{task_name}" in Task Scheduler, set to start at '
                    "login. It won't fire until your *next* actual log on or restart -- "
                    "checking Task Scheduler right now will correctly show it's never run "
                    "yet, and that's expected, not a failure. If you want to confirm it "
                    "works without waiting, find it in Task Scheduler Library, right-click "
                    "it, and choose Run."
                )
            return False, (
                "Windows: couldn't register the auto-start task automatically "
                f"(schtasks said: {_html.escape(result.stderr.strip() or result.stdout.strip())}). "
                "This is almost always a permissions issue -- close this wizard, "
                "right-click PowerShell or Command Prompt and choose &lsquo;Run as "
                "administrator,&rsquo; then re-run `python setup_wizard.py` from here "
                "and click Register now again. The app still works in the meantime, "
                "just needs `python watcher.py` run manually, or see the README for "
                "the manual Task Scheduler steps."
            )
        except Exception as e:
            return False, (
                f"Windows: couldn't register the auto-start task automatically ({e}). "
                "This is almost always a permissions issue -- close this wizard, "
                "right-click PowerShell or Command Prompt and choose &lsquo;Run as "
                "administrator,&rsquo; then re-run `python setup_wizard.py` from here "
                "and click Register now again. The app still works in the meantime, "
                "just needs `python watcher.py` run manually, or see the README for "
                "the manual Task Scheduler steps."
            )

    if sys.platform == "darwin":
        label = "com.journalrag.watcher"
        plist_dir = os.path.expanduser("~/Library/LaunchAgents")
        plist_path = os.path.join(plist_dir, f"{label}.plist")
        log_path = os.path.join(_PROJECT_ROOT, "watcher.log")
        plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>{label}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{python_exe}</string>
        <string>{watcher_path}</string>
    </array>
    <key>WorkingDirectory</key><string>{_PROJECT_ROOT}</string>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
    <key>StandardOutPath</key><string>{log_path}</string>
    <key>StandardErrorPath</key><string>{log_path}</string>
</dict>
</plist>
"""
        try:
            os.makedirs(plist_dir, exist_ok=True)
            with open(plist_path, "w", encoding="utf-8") as f:
                f.write(plist_content)
            subprocess.run(["launchctl", "unload", plist_path], capture_output=True, timeout=15)
            result = subprocess.run(
                ["launchctl", "load", "-w", plist_path],
                capture_output=True, text=True, timeout=15,
            )
            if result.returncode == 0:
                return True, (
                    "Mac: registered a LaunchAgent to start the watcher at login "
                    f"(log file at {log_path} if you ever want to check on it)."
                )
            return False, (
                "Mac: couldn't load the LaunchAgent automatically "
                f"(launchctl said: {_html.escape(result.stderr.strip() or result.stdout.strip())}). "
                "The app still works, just needs `python watcher.py` run manually."
            )
        except Exception as e:
            return False, (
                f"Mac: couldn't set up the LaunchAgent automatically ({e}). "
                "The app still works, just needs `python watcher.py` run manually."
            )

    return False, (
        "This OS isn't one this auto-start step covers (Windows and Mac are) -- "
        "run `python watcher.py` yourself, in a terminal left open, instead."
    )


def _autostart_preview():
    """Return a dict describing what _register_autostart would do, without doing it."""
    python_exe = sys.executable
    watcher_path = os.path.join(_PROJECT_ROOT, "watcher.py")

    if sys.platform == "win32":
        return {
            "platform": "Windows",
            "method": "Task Scheduler",
            "task_name": "MiniAI for DayOne & Diarium Watcher",
            "trigger": "At log on (runs every time you sign into Windows)",
            "command": f'"{python_exe}" "{watcher_path}"',
            "note": "This creates a scheduled task that starts the watcher automatically whenever you log in. It replaces any existing task with the same name.",
        }
    elif sys.platform == "darwin":
        return {
            "platform": "Mac",
            "method": "LaunchAgent",
            "task_name": "com.journalrag.watcher",
            "trigger": "At login (runs every time you log into your Mac)",
            "command": f"{python_exe} {watcher_path}",
            "note": "This installs a LaunchAgent plist that starts the watcher automatically at login and keeps it running.",
        }
    else:
        return {
            "platform": "Other",
            "method": "Manual",
            "task_name": "N/A",
            "trigger": "N/A",
            "command": f"python {watcher_path}",
            "note": "Auto-start isn't available on this OS. Run the watcher manually in a terminal.",
        }


def _is_safe_relative_path(path):
    """
    Guards the "create this folder" button against creating something
    outside the project, or somewhere surprising.
    """
    if not path or not path.strip():
        return False
    path = path.strip()
    if os.path.isabs(path):
        return False
    if ":" in path:
        return False
    normalized = os.path.normpath(path)
    if normalized.startswith("..") or normalized == "..":
        return False
    return True


def render_page(banner_html, env_values, path_values, tagging_enabled=False):
    page = _PAGE_TEMPLATE
    page = page.replace("__BANNER__", banner_html)

    # --- Provider selector: mark the saved provider as selected ---
    saved_provider = env_values.get("AI_PROVIDER", "anthropic")
    # Replace the matching option with a selected version
    provider_options = {
        "anthropic": "Anthropic (Claude)",
        "openai": "OpenAI (GPT)",
        "google": "Google (Gemini)",
        "mistral": "Mistral",
        "ollama": "Ollama (local, free)",
        "local_other": "Other local model (LM Studio, llama.cpp, etc.)",
    }
    for pid, plabel in provider_options.items():
        old_opt = f'<option value="{pid}">{plabel}</option>'
        sel = " selected" if pid == saved_provider else ""
        new_opt = f'<option value="{pid}"{sel}>{plabel}</option>'
        page = page.replace(old_opt, new_opt)

    # Show the right provider panel on load
    for pid in provider_options:
        div_id = f'id="provider-{pid}"'
        if pid == saved_provider:
            page = page.replace(
                f'{div_id} class="provider-config" style="display:none"',
                f'{div_id} class="provider-config"',
            )
        elif pid != saved_provider and f'{div_id} class="provider-config">' in page:
            # Make sure non-selected are hidden (Anthropic is shown by default in template)
            page = page.replace(
                f'{div_id} class="provider-config">',
                f'{div_id} class="provider-config" style="display:none">',
            )

    # --- API keys ---
    page = page.replace("__API_KEY_ANTHROPIC__", _html.escape(env_values.get("ANTHROPIC_API_KEY", "")))
    page = page.replace("__API_KEY_OPENAI__", _html.escape(env_values.get("OPENAI_API_KEY", "")))
    page = page.replace("__API_KEY_GOOGLE__", _html.escape(env_values.get("GOOGLE_API_KEY", "")))
    page = page.replace("__API_KEY_MISTRAL__", _html.escape(env_values.get("MISTRAL_API_KEY", "")))
    page = page.replace("__OLLAMA_URL__", _html.escape(env_values.get("OLLAMA_BASE_URL", "http://localhost:11434")))
    page = page.replace("__OLLAMA_MODEL__", _html.escape(env_values.get("OLLAMA_MODEL", "llama3.1")))
    page = page.replace("__LOCAL_OTHER_URL__", _html.escape(env_values.get("LOCAL_OTHER_BASE_URL", "http://localhost:1234")))
    page = page.replace("__API_KEY_LOCAL_OTHER__", _html.escape(env_values.get("LOCAL_OTHER_API_KEY", "")))
    page = page.replace("__LOCAL_OTHER_MODEL__", _html.escape(env_values.get("LOCAL_OTHER_MODEL", "")))

    # --- Model selectors: mark saved model as selected ---
    def _select_model(page, placeholder, saved_val):
        if saved_val:
            page = page.replace(placeholder, "selected")
        else:
            page = page.replace(placeholder, "")
        return page

    # For each provider, if the saved model isn't one of the built-in
    # options (e.g. it's a newer model typed into the "custom" box before
    # this wizard's dropdown knew about it), select "custom" and pre-fill
    # the text box with it -- so a hand-typed newer model survives being
    # displayed again, rather than silently reverting to a default.
    KNOWN_CLAUDE_MODELS = {"claude-haiku-4-5", "claude-sonnet-4-5", "claude-sonnet-4-6", "claude-opus-4"}
    KNOWN_OPENAI_MODELS = {"gpt-4o-mini", "gpt-4o", "gpt-4.1", "gpt-4.1-mini"}
    KNOWN_GEMINI_MODELS = {"gemini-2.0-flash", "gemini-2.5-flash", "gemini-2.5-pro"}
    KNOWN_MISTRAL_MODELS = {"mistral-small-latest", "mistral-medium-latest", "mistral-large-latest"}

    saved_claude = env_values.get("CLAUDE_MODEL", "claude-haiku-4-5")
    claude_is_custom = saved_claude not in KNOWN_CLAUDE_MODELS
    page = _select_model(page, "__MODEL_HAIKU__", saved_claude == "claude-haiku-4-5")
    page = _select_model(page, "__MODEL_SONNET45__", saved_claude == "claude-sonnet-4-5")
    page = _select_model(page, "__MODEL_SONNET46__", saved_claude == "claude-sonnet-4-6")
    page = _select_model(page, "__MODEL_OPUS__", saved_claude == "claude-opus-4")
    page = _select_model(page, "__MODEL_CLAUDE_CUSTOM__", claude_is_custom)
    page = page.replace("__CUSTOM_CLAUDE_MODEL__", _html.escape(saved_claude) if claude_is_custom else "")

    saved_openai = env_values.get("OPENAI_MODEL", "gpt-4o-mini")
    openai_is_custom = saved_openai not in KNOWN_OPENAI_MODELS
    page = _select_model(page, "__MODEL_4OMINI__", saved_openai == "gpt-4o-mini")
    page = _select_model(page, "__MODEL_4O__", saved_openai == "gpt-4o")
    page = _select_model(page, "__MODEL_41__", saved_openai == "gpt-4.1")
    page = _select_model(page, "__MODEL_41MINI__", saved_openai == "gpt-4.1-mini")
    page = _select_model(page, "__MODEL_OPENAI_CUSTOM__", openai_is_custom)
    page = page.replace("__CUSTOM_OPENAI_MODEL__", _html.escape(saved_openai) if openai_is_custom else "")

    saved_gemini = env_values.get("GEMINI_MODEL", "gemini-2.0-flash")
    gemini_is_custom = saved_gemini not in KNOWN_GEMINI_MODELS
    page = _select_model(page, "__MODEL_FLASH20__", saved_gemini == "gemini-2.0-flash")
    page = _select_model(page, "__MODEL_FLASH25__", saved_gemini == "gemini-2.5-flash")
    page = _select_model(page, "__MODEL_PRO25__", saved_gemini == "gemini-2.5-pro")
    page = _select_model(page, "__MODEL_GEMINI_CUSTOM__", gemini_is_custom)
    page = page.replace("__CUSTOM_GEMINI_MODEL__", _html.escape(saved_gemini) if gemini_is_custom else "")

    saved_mistral = env_values.get("MISTRAL_MODEL", "mistral-small-latest")
    mistral_is_custom = saved_mistral not in KNOWN_MISTRAL_MODELS
    page = _select_model(page, "__MODEL_MSMALL__", saved_mistral == "mistral-small-latest")
    page = _select_model(page, "__MODEL_MMED__", saved_mistral == "mistral-medium-latest")
    page = _select_model(page, "__MODEL_MLARGE__", saved_mistral == "mistral-large-latest")
    page = _select_model(page, "__MODEL_MISTRAL_CUSTOM__", mistral_is_custom)
    page = page.replace("__CUSTOM_MISTRAL_MODEL__", _html.escape(saved_mistral) if mistral_is_custom else "")

    page = page.replace("__TAGGING_CHECKED__", "checked" if tagging_enabled else "")

    cloud_providers = _resolve_cloud_paths()

    required_fields_html = []
    advanced_fields_html = []
    for field in PATH_FIELDS:
        value = path_values.get(field["env_key"], field["default"])

        # Cloud provider grid -- only for the export folder field
        cloud_html = ""
        if field["env_key"] == "JOURNAL_SYNC_DIR":
            cards = ""
            for prov in cloud_providers:
                fill_path = prov["default_path"]
                if prov["id"] == "other":
                    onclick = "browseForFolder(" + _json.dumps(field["env_key"]) + ")"
                else:
                    onclick = "pickCloudProvider(" + _json.dumps(field["env_key"]) + ", " + _json.dumps(fill_path) + ", " + _json.dumps(prov["label"]) + ")"
                cards += (
                    f'<button type="button" class="cloud-card" '
                    f'style="--provider-color: {prov["color"]}" '
                    f'onclick="{_html.escape(onclick)}">'
                    f'<div class="cloud-icon">{prov["icon"]}</div>'
                    f'<div class="cloud-label">{prov["label"]}</div>'
                    f'</button>'
                )
            cloud_html = (
                '<div class="cloud-section">'
                '<p class="cloud-heading">Pick a sync folder, or choose your own:</p>'
                f'<div class="cloud-grid">{cards}</div>'
                '</div>'
            )

        # Browse button -- always present on every path field. The onclick
        # value must go through _html.escape() (not just _json.dumps()) --
        # json.dumps produces a string wrapped in real double-quote
        # characters, and this whole thing is embedded inside an
        # onclick="..." HTML attribute that ALSO uses double quotes. Without
        # escaping, the raw " from json.dumps closes the attribute early and
        # the browser silently fails to parse the rest as a handler at all --
        # no console error, no server request, just a dead button. (The
        # cloud-card buttons a few lines up get this right already; this one
        # didn't match that pattern.)
        browse_onclick = "browseForFolder(" + _json.dumps(field["env_key"]) + ")"
        browse_btn = (
            f'<button type="button" class="browse-btn" '
            f'onclick="{_html.escape(browse_onclick)}">'
            '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 7a2 2 0 012-2h4l2 2h8a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2V7z"/></svg>'
            ' Browse&hellip;'
            '</button>'
        )

        field_html = f"""
    <label>{field["label"]}
      <div class="hint">{field["hint"]}</div>
    </label>
    {cloud_html}
    <div class="path-row">
      <input type="text" name="{field["env_key"]}" value="{_html.escape(value)}" placeholder="{field["default"]}" id="field-{field["env_key"]}">
      {browse_btn}
      <button type="button" class="create-btn" onclick="createFolder('{field["env_key"]}')">Create</button>
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
    max-width: 600px;
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
  .custom-model-wrap { margin-top: 8px; }
  .path-row { display: flex; gap: 6px; align-items: flex-start; }
  .path-row input { flex: 1; }
  .create-btn, .install-btn {
    margin-top: 6px;
    background: #E8E1D2;
    color: #232C3D;
    border: none;
    padding: 10px 12px;
    border-radius: 6px;
    font-size: 13px;
    cursor: pointer;
    white-space: nowrap;
  }
  .browse-btn {
    margin-top: 6px;
    background: #5E7052;
    color: white;
    border: none;
    padding: 10px 12px;
    border-radius: 6px;
    font-size: 13px;
    cursor: pointer;
    white-space: nowrap;
    display: inline-flex;
    align-items: center;
    gap: 4px;
  }
  .browse-btn:hover { background: #7C9070; }
  .browse-btn svg { flex-shrink: 0; }
  .create-btn:hover, .install-btn:hover { background: #DCD4C2; }
  .create-btn:disabled, .install-btn:disabled { opacity: 0.6; cursor: default; }
  .folder-status { font-size: 12.5px; margin-top: 4px; min-height: 16px; }
  .folder-status.ok { color: #4A6741; }
  .folder-status.err { color: #A14E3C; }

  /* Cloud provider grid */
  .cloud-section { margin-top: 12px; }
  .cloud-heading { font-size: 13px; font-weight: 600; color: #4A5568; margin: 0 0 8px; }
  .cloud-grid {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 8px;
  }
  .cloud-card {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 4px;
    padding: 12px 6px 10px;
    border: 2px solid #E8E1D2;
    border-radius: 10px;
    background: white;
    cursor: pointer;
    position: relative;
    transition: border-color 0.15s, box-shadow 0.15s;
  }
  .cloud-card:hover {
    border-color: var(--provider-color, #5E7052);
    box-shadow: 0 2px 8px rgba(0,0,0,0.08);
  }
  .cloud-icon {
    width: 28px;
    height: 28px;
    color: var(--provider-color, #6B7280);
  }
  .cloud-icon svg { width: 100%; height: 100%; }
  .cloud-label { font-size: 11px; font-weight: 600; color: #232C3D; text-align: center; line-height: 1.2; }
  /* Autostart section */
  .autostart-section {
    background: #F3EFE3;
    border-radius: 8px;
    padding: 16px;
    margin-top: 14px;
  }
  .autostart-section strong { font-size: 14px; }
  .autostart-section p { font-size: 13px; margin-top: 4px; color: #4A5568; }
  .autostart-preview {
    background: #232C3D;
    color: #D4E8CB;
    font-family: 'SF Mono', Consolas, monospace;
    font-size: 12px;
    padding: 12px 14px;
    border-radius: 6px;
    margin-top: 10px;
    display: none;
  }
  .autostart-preview .preview-row { margin: 4px 0; }
  .autostart-preview .preview-label { color: #8B9C82; }
  .autostart-preview .preview-value { color: #E8E1D2; }
  .autostart-btn {
    margin-top: 10px;
    background: #5E7052;
    color: white;
    border: none;
    padding: 10px 16px;
    border-radius: 6px;
    font-size: 13px;
    cursor: pointer;
  }
  .autostart-btn:hover { background: #7C9070; }
  .autostart-btn:disabled { opacity: 0.6; cursor: default; }
  .autostart-btn.secondary {
    background: #E8E1D2;
    color: #232C3D;
  }
  .autostart-btn.secondary:hover { background: #DCD4C2; }
  .autostart-result {
    font-size: 13px;
    margin-top: 8px;
    padding: 10px 14px;
    border-radius: 6px;
    display: none;
  }
  .autostart-result.ok { background: #DCE8D4; color: #3F4F38; display: block; }
  .autostart-result.err { background: #F3E6DE; color: #7A4128; display: block; }

  .autostart-note {
    background: #EDF1E8;
    border: 1px solid #D3DFC9;
    border-radius: 6px;
    padding: 10px 14px;
    font-size: 12.5px;
    margin-top: 10px;
    color: #3F4F38;
    white-space: pre-wrap;
  }
  .autostart-note.warn { background: #F3E6DE; border-color: #E0BBA6; color: #7A4128; }
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
  .cost-table { width: 100%; border-collapse: collapse; margin: 10px 0 6px; font-size: 13px; background: #fff; border-radius: 4px; }
  .cost-table th, .cost-table td { text-align: left; padding: 6px 10px; }
  .cost-table th { background: #DCD3BF; }
  .cost-table td { border-top: 1px solid #E8E1D2; }
  .cost-table code { background: transparent; padding: 0; }
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

  @media (max-width: 480px) {
    .cloud-grid { grid-template-columns: repeat(3, 1fr); }
  }
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
    <h2>Step 2: Your AI provider</h2>
    <div class="banner warn">
      <strong>This app is free to use, and we don't earn anything from it.</strong>
      Using it isn't free, though: asking questions and tagging entries call whichever
      provider you pick below, and <strong>that provider bills you directly</strong> &mdash;
      every dollar goes to them, none to us.
      <table class="cost-table">
        <tr><th>Provider &amp; model</th><th>Input</th><th>Output</th><th>~1,000 entries</th></tr>
        <tr><th colspan="4" style="background:#e8e1d2;font-size:12px;text-transform:uppercase;letter-spacing:.5px">Budget tier (tagging default)</th></tr>
        <tr><td>OpenAI (<code>gpt-4o-mini</code>)</td><td>$0.15/M</td><td>$0.60/M</td><td><strong>~$0.05</strong></td></tr>
        <tr><td>Google (<code>gemini-2.0-flash</code>)</td><td>$0.15/M</td><td>$0.60/M</td><td><strong>~$0.05</strong></td></tr>
        <tr><td>Mistral (<code>mistral-small</code>)</td><td>$0.15/M</td><td>$0.60/M</td><td><strong>~$0.05</strong></td></tr>
        <tr><td>Anthropic (<code>claude-haiku-4-5</code>)</td><td>$1.00/M</td><td>$5.00/M</td><td><strong>~$0.39</strong></td></tr>
        <tr><th colspan="4" style="background:#e8e1d2;font-size:12px;text-transform:uppercase;letter-spacing:.5px">Chat tier (question-answering)</th></tr>
        <tr><td>OpenAI (<code>gpt-4.1-mini</code>)</td><td>$0.40/M</td><td>$1.60/M</td><td><strong>~$0.14</strong></td></tr>
        <tr><td>Google (<code>gemini-2.5-pro</code>)</td><td>$1.25/M</td><td>$10.00/M</td><td><strong>~$0.61</strong></td></tr>
        <tr><td>Mistral (<code>mistral-medium</code>)</td><td>$0.40/M</td><td>$2.00/M</td><td><strong>~$0.15</strong></td></tr>
        <tr><td>Anthropic (<code>claude-sonnet-4-5</code>)</td><td>$3.00/M</td><td>$15.00/M</td><td><strong>~$1.16</strong></td></tr>
        <tr><th colspan="4" style="background:#e8e1d2;font-size:12px;text-transform:uppercase;letter-spacing:.5px">Local (free)</th></tr>
        <tr><td>Ollama / LM Studio / other local</td><td>$0</td><td>$0</td><td><strong>$0</strong></td></tr>
      </table>
      Ollama / LM Studio / other local models run on your own computer &mdash; free,
      but usually need <strong>16GB+ RAM</strong> to run a model capable enough to be useful,
      and expect reduced functionality and occasional setup friction compared to a hosted provider.
    </div>
    <label>Provider
      <div class="hint">Which AI service should power your journal Q&amp;A?</div>
    </label>
    <select name="AI_PROVIDER" id="ai-provider-select" onchange="updateProviderUI()">
      <option value="anthropic">Anthropic (Claude)</option>
      <option value="openai">OpenAI (GPT)</option>
      <option value="google">Google (Gemini)</option>
      <option value="mistral">Mistral</option>
      <option value="ollama">Ollama (local, free)</option>
      <option value="local_other">Other local model (LM Studio, llama.cpp, etc.)</option>
    </select>

    <div id="provider-anthropic" class="provider-config">
      <label>Anthropic API key
        <div class="hint">From <a href="https://console.anthropic.com" target="_blank">console.anthropic.com</a> &rarr; Settings &rarr; API Keys.<br>Already saved a key below? You don't need to re-enter it &mdash; it's kept as-is unless you type a new one.</div>
      </label>
      <input type="password" name="ANTHROPIC_API_KEY" value="__API_KEY_ANTHROPIC__" placeholder="sk-ant-...">
      <label>Model
        <div class="hint">Haiku is cheapest; Sonnet balances cost and quality; Opus is the most capable.</div>
      </label>
      <select name="CLAUDE_MODEL" id="select-CLAUDE_MODEL" onchange="toggleCustomModel('CLAUDE_MODEL')">
        <option value="claude-haiku-4-5" __MODEL_HAIKU__>Claude Haiku 4.5 (cheapest)</option>
        <option value="claude-sonnet-4-5" __MODEL_SONNET45__>Claude Sonnet 4.5</option>
        <option value="claude-sonnet-4-6" __MODEL_SONNET46__>Claude Sonnet 4.6</option>
        <option value="claude-opus-4" __MODEL_OPUS__>Claude Opus 4 (most capable)</option>
        <option value="custom" __MODEL_CLAUDE_CUSTOM__>A newer model not listed here&hellip;</option>
      </select>
      <div id="custom-wrap-CLAUDE_MODEL" class="custom-model-wrap" style="display:none">
        <input type="text" id="custom-CLAUDE_MODEL" name="CLAUDE_MODEL_CUSTOM" placeholder="e.g. claude-opus-5" value="__CUSTOM_CLAUDE_MODEL__">
        <div class="hint">Type the exact model ID &mdash; find current ones at <a href="https://docs.claude.com/en/docs/about-claude/models" target="_blank">docs.claude.com</a>.</div>
      </div>
    </div>

    <div id="provider-openai" class="provider-config" style="display:none">
      <label>OpenAI API key
        <div class="hint">From <a href="https://platform.openai.com/api-keys" target="_blank">platform.openai.com</a>.<br>Already saved a key below? You don't need to re-enter it &mdash; it's kept as-is unless you type a new one.</div>
      </label>
      <input type="password" name="OPENAI_API_KEY" value="__API_KEY_OPENAI__" placeholder="sk-...">
      <label>Model</label>
      <select name="OPENAI_MODEL" id="select-OPENAI_MODEL" onchange="toggleCustomModel('OPENAI_MODEL')">
        <option value="gpt-4o-mini" __MODEL_4OMINI__>GPT-4o mini (cheapest)</option>
        <option value="gpt-4o" __MODEL_4O__>GPT-4o</option>
        <option value="gpt-4.1" __MODEL_41__>GPT-4.1</option>
        <option value="gpt-4.1-mini" __MODEL_41MINI__>GPT-4.1 mini</option>
        <option value="custom" __MODEL_OPENAI_CUSTOM__>A newer model not listed here&hellip;</option>
      </select>
      <div id="custom-wrap-OPENAI_MODEL" class="custom-model-wrap" style="display:none">
        <input type="text" id="custom-OPENAI_MODEL" name="OPENAI_MODEL_CUSTOM" placeholder="e.g. gpt-5.4-mini" value="__CUSTOM_OPENAI_MODEL__">
        <div class="hint">Type the exact model ID &mdash; find current ones at <a href="https://platform.openai.com/docs/models" target="_blank">platform.openai.com/docs/models</a>.</div>
      </div>
    </div>

    <div id="provider-google" class="provider-config" style="display:none">
      <label>Google AI API key
        <div class="hint">From <a href="https://aistudio.google.com/apikey" target="_blank">aistudio.google.com</a>.<br>Already saved a key below? You don't need to re-enter it &mdash; it's kept as-is unless you type a new one.</div>
      </label>
      <input type="password" name="GOOGLE_API_KEY" value="__API_KEY_GOOGLE__" placeholder="AI...">
      <label>Model</label>
      <select name="GEMINI_MODEL" id="select-GEMINI_MODEL" onchange="toggleCustomModel('GEMINI_MODEL')">
        <option value="gemini-2.0-flash" __MODEL_FLASH20__>Gemini 2.0 Flash (cheapest)</option>
        <option value="gemini-2.5-flash" __MODEL_FLASH25__>Gemini 2.5 Flash</option>
        <option value="gemini-2.5-pro" __MODEL_PRO25__>Gemini 2.5 Pro</option>
        <option value="custom" __MODEL_GEMINI_CUSTOM__>A newer model not listed here&hellip;</option>
      </select>
      <div id="custom-wrap-GEMINI_MODEL" class="custom-model-wrap" style="display:none">
        <input type="text" id="custom-GEMINI_MODEL" name="GEMINI_MODEL_CUSTOM" placeholder="e.g. gemini-3.5-flash" value="__CUSTOM_GEMINI_MODEL__">
        <div class="hint">Type the exact model ID &mdash; find current ones at <a href="https://ai.google.dev/gemini-api/docs/models" target="_blank">ai.google.dev/gemini-api/docs/models</a>.</div>
      </div>
    </div>

    <div id="provider-mistral" class="provider-config" style="display:none">
      <label>Mistral API key
        <div class="hint">From <a href="https://console.mistral.ai/api-keys" target="_blank">console.mistral.ai</a>.<br>Already saved a key below? You don't need to re-enter it &mdash; it's kept as-is unless you type a new one.</div>
      </label>
      <input type="password" name="MISTRAL_API_KEY" value="__API_KEY_MISTRAL__" placeholder="...">
      <label>Model</label>
      <select name="MISTRAL_MODEL" id="select-MISTRAL_MODEL" onchange="toggleCustomModel('MISTRAL_MODEL')">
        <option value="mistral-small-latest" __MODEL_MSMALL__>Mistral Small (cheapest)</option>
        <option value="mistral-medium-latest" __MODEL_MMED__>Mistral Medium</option>
        <option value="mistral-large-latest" __MODEL_MLARGE__>Mistral Large</option>
        <option value="custom" __MODEL_MISTRAL_CUSTOM__>A newer model not listed here&hellip;</option>
      </select>
      <div id="custom-wrap-MISTRAL_MODEL" class="custom-model-wrap" style="display:none">
        <input type="text" id="custom-MISTRAL_MODEL" name="MISTRAL_MODEL_CUSTOM" placeholder="e.g. mistral-large-2" value="__CUSTOM_MISTRAL_MODEL__">
        <div class="hint">Type the exact model ID &mdash; find current ones at <a href="https://docs.mistral.ai/getting-started/models/" target="_blank">docs.mistral.ai/getting-started/models</a>.</div>
      </div>
    </div>

    <div id="provider-ollama" class="provider-config" style="display:none">
      <label>Ollama server URL
        <div class="hint">Default is http://localhost:11434. Install Ollama from <a href="https://ollama.com" target="_blank">ollama.com</a> and pull a model first.</div>
      </label>
      <input type="text" name="OLLAMA_BASE_URL" value="__OLLAMA_URL__" placeholder="http://localhost:11434">
      <label>Model
        <div class="hint">Must already be pulled locally (e.g. <code>ollama pull llama3.1</code>).</div>
      </label>
      <input type="text" name="OLLAMA_MODEL" value="__OLLAMA_MODEL__" placeholder="llama3.1">
    </div>

    <div id="provider-local_other" class="provider-config" style="display:none">
      <label>Local server URL
        <div class="hint">The OpenAI-compatible API endpoint your local model exposes.<br>LM Studio: <code>http://localhost:1234</code> &bull; llama.cpp: <code>http://localhost:8080</code></div>
      </label>
      <input type="text" name="LOCAL_OTHER_BASE_URL" value="__LOCAL_OTHER_URL__" placeholder="http://localhost:1234">
      <label>API key (optional)
        <div class="hint">Most local servers don&rsquo;t require one. Leave blank unless your server is configured to require authentication.</div>
      </label>
      <input type="password" name="LOCAL_OTHER_API_KEY" value="__API_KEY_LOCAL_OTHER__" placeholder="(leave blank if not needed)">
      <label>Model name
        <div class="hint">The model identifier your server uses &mdash; check your server&rsquo;s loaded-model list.</div>
      </label>
      <input type="text" name="LOCAL_OTHER_MODEL" value="__LOCAL_OTHER_MODEL__" placeholder="e.g. mistral-7b-instruct">
    </div>

    <p class="hint" style="margin-top:8px">Tagging (Step 3) always uses the cheapest model available from your chosen provider, regardless of which model you pick above.</p>

    <h2>Step 3: Tagging &amp; Max Recall</h2>
    <div class="tagging-note">
      <p>
        Tagging reads each entry once to note the people, places, and themes in it &mdash;
        this is what lets <strong>Max Recall</strong> pull up <em>every</em> entry about a topic,
        not just the closest few matches. It costs a small amount per entry &mdash; as a
        reference, tagging a first-time journal of about 1,000 entries costs roughly
        <strong>$0.05</strong> with OpenAI, Google, or Mistral's cheapest models,
        roughly <strong>$0.35&ndash;$0.40</strong> with Claude Haiku (still cheap in
        absolute terms, just the priciest of this group per token), and <strong>$0</strong>
        with a local Ollama model. See the README for the full per-provider breakdown and
        how these numbers are estimated. On a first-time ingest of a large journal this can
        add up before you've decided you want it &mdash; so it's off by default.
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
      (<code>.env</code>) that lives only in this project folder, on this computer.
      <br><br>
      The only information that may be sent out are the journals that are processed when you
      ask your AI agent of choice. It may be a good choice of practice to strip away any
      sensitive information and store it in a different, secure drive prior to using the
      mini journal.
      <br><br>
      You may link your journal agent from a public one to a private one, however understand
      that it may sacrifice some functionality.
      <br><br>
      We have excluded config files from this project's git repository so they don't end up
      on GitHub.
    </div>

  <div class="autostart-section" id="autostart-section">
    <strong>Step 5: Start on login (recommended)</strong>
    <p>Register the watcher so it starts automatically every time you log in.
       This means new journal exports are picked up and ingested without you
       having to do anything.</p>
    <button type="button" class="autostart-btn secondary" id="autostart-preview-btn"
            onclick="showAutostartPreview()">
      Show what this will do
    </button>
    <div class="autostart-preview" id="autostart-preview"></div>
    <div style="display:none" id="autostart-confirm-area">
      <button type="button" class="autostart-btn" id="autostart-confirm-btn"
              onclick="registerAutostart()">
        Register now
      </button>
    </div>
    <div class="autostart-result" id="autostart-result"></div>
  </div>

    <button type="submit">Save</button>
  </form>

<script>
function updateProviderUI() {
  var provider = document.getElementById('ai-provider-select').value;
  var configs = document.querySelectorAll('.provider-config');
  for (var i = 0; i < configs.length; i++) configs[i].style.display = 'none';
  var active = document.getElementById('provider-' + provider);
  if (active) active.style.display = 'block';
  // Mark inputs in hidden providers as not required
  var allInputs = document.querySelectorAll('.provider-config input, .provider-config select');
  for (var i = 0; i < allInputs.length; i++) allInputs[i].removeAttribute('required');
  // Mark inputs in visible provider as required (except Ollama URL which has a default)
  if (active) {
    var visibleInputs = active.querySelectorAll('input[type=password]');
    for (var i = 0; i < visibleInputs.length; i++) visibleInputs[i].setAttribute('required', 'required');
  }
}
// Run on page load to show the right provider panel
document.addEventListener('DOMContentLoaded', function() { updateProviderUI(); });

// Shows/hides the free-text "type your own model ID" box next to a
// provider's model dropdown. This is what keeps a brand-new model (one
// that came out after this wizard's dropdown list was last updated) from
// ever being a hard blocker -- the actual API call (see llm.py) accepts
// any model ID string, so typing a new one here works immediately, no
// code changes needed anywhere.
function toggleCustomModel(fieldName) {
  var select = document.getElementById('select-' + fieldName);
  var wrap = document.getElementById('custom-wrap-' + fieldName);
  if (!select || !wrap) return;
  wrap.style.display = (select.value === 'custom') ? 'block' : 'none';
}
// Run once on load for every provider's dropdown, in case a previously
// saved custom model needs its box shown (and pre-filled) immediately.
document.addEventListener('DOMContentLoaded', function() {
  ['CLAUDE_MODEL', 'OPENAI_MODEL', 'GEMINI_MODEL', 'MISTRAL_MODEL'].forEach(toggleCustomModel);
});

function fillField(envKey, path) {
  document.getElementById('field-' + envKey).value = path;
}

function pickCloudProvider(envKey, defaultPath, label) {
  var field = document.getElementById('field-' + envKey);
  var status = document.getElementById('status-' + envKey);
  if (defaultPath) {
    field.value = defaultPath;
    status.className = 'folder-status ok';
    status.textContent = 'Set to ' + label + ' folder. You can edit the path above or use Browse to pick a subfolder.';
  } else {
    // No default path (shouldn't happen for named providers, but just in case)
    browseForFolder(envKey);
  }
}

function browseForFolder(envKey) {
  var status = document.getElementById('status-' + envKey);
  status.className = 'folder-status';
  status.textContent = 'Opening folder picker... (a Windows dialog should appear — it may be behind this window)';
  fetch('/browse', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({env_key: envKey})})
    .then(function(r) {
      if (!r.ok) throw new Error('Server returned ' + r.status);
      return r.json();
    })
    .then(function(data) {
      if (data.ok && data.path) {
        document.getElementById('field-' + envKey).value = data.path;
        status.className = 'folder-status ok';
        status.textContent = 'Selected: ' + data.path;
      } else if (data.cancelled) {
        status.className = 'folder-status';
        status.textContent = '';
      } else {
        status.className = 'folder-status err';
        status.textContent = data.error || 'Could not open folder picker.';
      }
    })
    .catch(function() {
      status.className = 'folder-status err';
      status.textContent = 'Something went wrong opening the folder picker.';
    });
}

function createFolder(envKey) {
  var input = document.getElementById('field-' + envKey);
  var status = document.getElementById('status-' + envKey);
  var btn = event.target;
  btn.disabled = true;
  status.className = 'folder-status';
  status.textContent = 'Creating...';
  fetch('/create-folder', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({path: input.value})
  })
    .then(function(r) { return r.json(); })
    .then(function(data) {
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
    .catch(function() {
      btn.disabled = false;
      status.className = 'folder-status err';
      status.textContent = 'Something went wrong -- check the terminal window for details.';
    });
}

function showAutostartPreview() {
  var previewDiv = document.getElementById('autostart-preview');
  var confirmArea = document.getElementById('autostart-confirm-area');
  var btn = document.getElementById('autostart-preview-btn');
  btn.disabled = true;
  btn.textContent = 'Loading...';
  fetch('/autostart-preview')
    .then(function(r) { return r.json(); })
    .then(function(data) {
      previewDiv.innerHTML =
        '<div class="preview-row"><span class="preview-label">Platform: </span><span class="preview-value">' + data.platform + '</span></div>' +
        '<div class="preview-row"><span class="preview-label">Method: </span><span class="preview-value">' + data.method + '</span></div>' +
        '<div class="preview-row"><span class="preview-label">Task name: </span><span class="preview-value">' + data.task_name + '</span></div>' +
        '<div class="preview-row"><span class="preview-label">Trigger: </span><span class="preview-value">' + data.trigger + '</span></div>' +
        '<div class="preview-row"><span class="preview-label">Command: </span><span class="preview-value">' + data.command + '</span></div>' +
        '<div class="preview-row" style="margin-top:8px;color:#8B9C82;font-style:italic;">' + data.note + '</div>';
      previewDiv.style.display = 'block';
      confirmArea.style.display = 'block';
      btn.textContent = 'Show what this will do';
      btn.disabled = false;
    })
    .catch(function() {
      btn.textContent = 'Show what this will do';
      btn.disabled = false;
    });
}

function registerAutostart() {
  var btn = document.getElementById('autostart-confirm-btn');
  var result = document.getElementById('autostart-result');
  btn.disabled = true;
  btn.textContent = 'Registering...';
  result.style.display = 'none';
  fetch('/autostart-register', {method: 'POST'})
    .then(function(r) { return r.json(); })
    .then(function(data) {
      btn.disabled = false;
      btn.textContent = data.ok ? 'Done!' : 'Register now';
      result.className = 'autostart-result ' + (data.ok ? 'ok' : 'err');
      result.textContent = data.message;
      result.style.display = 'block';
    })
    .catch(function() {
      btn.disabled = false;
      btn.textContent = 'Register now';
      result.className = 'autostart-result err';
      result.textContent = 'Something went wrong -- check the terminal window.';
      result.style.display = 'block';
    });
}

function togglePhotoSearch() {
  var checked = document.getElementById('photo-search-toggle').checked;
  document.getElementById('photo-search-note').style.display = checked ? 'block' : 'none';
  document.getElementById('photo-install-btn').style.display = checked ? 'inline-block' : 'none';
}

function startInstall(which) {
  var btnId = which === 'core' ? 'core-install-btn' : 'photo-install-btn';
  var logId = which === 'core' ? 'install-log' : 'photo-install-log';
  var btn = document.getElementById(btnId);
  var log = document.getElementById(logId);
  btn.disabled = true;
  btn.textContent = 'Installing...';
  log.style.display = 'block';
  log.textContent = 'Starting install -- this can take a minute or two' +
    (which === 'photo' ? ' (longer for photo search -- it includes a large download)' : '') + '...\\n';

  fetch('/install/' + which, {method: 'POST'})
    .then(function() { pollInstallStatus(which, btn, log); });
}

function pollInstallStatus(which, btn, log) {
  fetch('/install-status/' + which)
    .then(function(r) { return r.json(); })
    .then(function(data) {
      log.textContent = data.log.join('\\n');
      log.scrollTop = log.scrollHeight;
      if (data.done) {
        btn.disabled = false;
        btn.textContent = data.success ? 'Done ✓' : 'Failed -- click to retry';
      } else {
        setTimeout(function() { pollInstallStatus(which, btn, log); }, 1000);
      }
    })
    .catch(function() {
      setTimeout(function() { pollInstallStatus(which, btn, log); }, 1500);
    });
}
</script>
</body>
</html>
"""


def _tagging_enabled_from(values):
    """Reads the ENABLE_TAGGING value out of a values dict the same way
    config.py's own parsing does."""
    return (values.get("ENABLE_TAGGING") or "").strip().lower() == "true"


@app.route("/", methods=["GET"])
def index():
    existing = read_existing_env()
    banner = ""
    # Check if any provider API key is set
    has_key = any(existing.get(k) for k in (
        "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GOOGLE_API_KEY",
        "MISTRAL_API_KEY", "OLLAMA_BASE_URL",
    ))
    if has_key:
        banner = '<div class="banner">Already configured. Change anything below and save again to update it.</div>'
    return render_page(
        banner,
        existing,
        existing,
        tagging_enabled=_tagging_enabled_from(existing),
    )


# Map of provider -> which API key field to validate (None = no key needed)
_PROVIDER_KEY_FIELDS = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "google": "GOOGLE_API_KEY",
    "mistral": "MISTRAL_API_KEY",
    "ollama": None,  # Ollama is local, no API key
    "local_other": None,  # local model, API key is optional (handled separately)
}

# Map of provider -> which model field(s) to save
_PROVIDER_MODEL_FIELDS = {
    "anthropic": ["CLAUDE_MODEL"],
    "openai": ["OPENAI_MODEL"],
    "google": ["GEMINI_MODEL"],
    "mistral": ["MISTRAL_MODEL"],
    "ollama": ["OLLAMA_MODEL", "OLLAMA_BASE_URL"],
    "local_other": ["LOCAL_OTHER_MODEL", "LOCAL_OTHER_BASE_URL", "LOCAL_OTHER_API_KEY"],
}


@app.route("/save", methods=["POST"])
def save():
    provider = (request.form.get("AI_PROVIDER") or "anthropic").strip()

    path_values = {}
    for field in PATH_FIELDS:
        raw = (request.form.get(field["env_key"]) or "").strip()
        path_values[field["env_key"]] = raw or field["default"]

    tagging_enabled = "ENABLE_TAGGING" in request.form

    # Validate the API key for the chosen provider (skip for Ollama)
    key_field = _PROVIDER_KEY_FIELDS.get(provider)
    api_key = ""
    if key_field:
        api_key = (request.form.get(key_field) or "").strip()
        if len(api_key) < 10:
            banner = ('<div class="banner warn">That doesn&#39;t look like a full API key -- '
                       'please paste the whole thing.</div>')
            form_values = read_existing_env()
            form_values.update(path_values)
            form_values["AI_PROVIDER"] = provider
            if api_key:
                form_values[key_field] = api_key
            return render_page(banner, form_values, path_values, tagging_enabled=tagging_enabled)

    values = read_existing_env()
    values["AI_PROVIDER"] = provider
    if key_field and api_key:
        values[key_field] = api_key
    # Save model choices for the active provider. If a dropdown's value is
    # "custom", the real model ID comes from its companion "<FIELD>_CUSTOM"
    # text box instead -- this is what lets a model newer than this
    # wizard's dropdown list work immediately, with no code changes.
    for mf in _PROVIDER_MODEL_FIELDS.get(provider, []):
        val = (request.form.get(mf) or "").strip()
        if val == "custom":
            val = (request.form.get(mf + "_CUSTOM") or "").strip()
        if val:
            values[mf] = val
    values.update(path_values)
    values["ENABLE_TAGGING"] = "true" if tagging_enabled else "false"
    write_env(values)

    tagging_note = (
        "Tagging is on -- new entries will be tagged automatically at ingest time."
        if tagging_enabled else
        "Tagging is off -- ingest.py will skip tag extraction (no related API "
        "calls) until you turn it back on here."
    )

    if _EMBEDDED_LAUNCH:
        banner = (
            '<div class="banner success">Saved! ' + tagging_note
            + ' You can close this tab now &mdash; this settings window will stop itself in a few seconds.</div>'
        )

        def _delayed_exit():
            time.sleep(3)
            os._exit(0)

        threading.Thread(target=_delayed_exit, daemon=True).start()
    else:
        banner = (
            '<div class="banner success">Saved! ' + tagging_note
            + ' Use Step 5 below to set up auto-start if you haven\'t already.</div>'
        )
    return render_page(banner, values, path_values, tagging_enabled=tagging_enabled)


@app.route("/create-folder", methods=["POST"])
def create_folder():
    """Creates a folder relative to the project root."""
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


@app.route("/browse", methods=["POST"])
def browse():
    """Opens the native OS folder picker dialog.

    On Windows: uses PowerShell's FolderBrowserDialog (always available,
    no extra dependencies). On macOS: uses osascript to open a native
    Finder folder-picker dialog. Both run as subprocesses so the Flask
    server stays responsive.
    """
    try:
        if sys.platform == "win32":
            # Windows folder picker via PowerShell + WinForms.
            #
            # Two things caused the dialog to silently never appear in
            # earlier versions, and both are fixed here:
            #   1. -STA  -- WinForms dialogs REQUIRE a single-threaded
            #      apartment. PowerShell does not always default to STA, and
            #      without it FolderBrowserDialog can fail to show at all,
            #      with no error printed anywhere. This is the main fix.
            #   2. A TopMost owner form -- a dialog opened from a background
            #      process often appears BEHIND the browser window, so it
            #      looks like nothing happened. Parenting it to a hidden,
            #      always-on-top form forces it to the front.
            #
            # The print() calls below are intentional: they show up in the
            # terminal running setup_wizard.py, so if the picker ever
            # misbehaves again you can see exactly what came back.
            print("[browse] Opening Windows folder picker -- a dialog should "
                  "appear in front. If you don't see it, check behind this "
                  "window or the taskbar.", flush=True)
            ps_script = (
                "Add-Type -AssemblyName System.Windows.Forms;"
                "Add-Type -AssemblyName System.Drawing;"
                "$owner = New-Object System.Windows.Forms.Form;"
                "$owner.TopMost = $true; $owner.ShowInTaskbar = $false;"
                "$owner.Opacity = 0; $owner.Show(); $owner.Activate();"
                "$d = New-Object System.Windows.Forms.FolderBrowserDialog;"
                "$d.Description = 'Choose your journal folder';"
                "$d.ShowNewFolderButton = $true;"
                "$r = $d.ShowDialog($owner); $owner.Close();"
                "if ($r -eq [System.Windows.Forms.DialogResult]::OK) "
                "{ Write-Output $d.SelectedPath } else { Write-Output '::CANCELLED::' }"
            )
            result = subprocess.run(
                ["powershell", "-NoProfile", "-STA", "-Command", ps_script],
                capture_output=True, text=True, timeout=180,
            )
            chosen = (result.stdout or "").strip()
            print(f"[browse] picker returned rc={result.returncode} "
                  f"path={chosen!r} err={(result.stderr or '').strip()!r}", flush=True)
            if chosen and chosen != "::CANCELLED::":
                return jsonify({"ok": True, "path": chosen})
            if chosen == "::CANCELLED::":
                return jsonify({"ok": False, "cancelled": True})

            # PowerShell gave nothing usable -- fall back to a VBScript COM
            # dialog. Some locked-down machines restrict PowerShell but still
            # allow cscript, so this is a genuine second chance, not a repeat.
            print("[browse] PowerShell gave no path; trying VBScript fallback...", flush=True)
            import tempfile
            vbs = (
                'Set objShell = CreateObject("Shell.Application")\n'
                'Set objFolder = objShell.BrowseForFolder(0, "Choose a folder", &H0040, "")\n'
                'If objFolder Is Nothing Then\n'
                '    WScript.StdOut.Write "::CANCELLED::"\n'
                'Else\n'
                '    WScript.StdOut.Write objFolder.Self.Path\n'
                'End If\n'
            )
            vbs_path = os.path.join(tempfile.gettempdir(), "_journal_rag_browse.vbs")
            with open(vbs_path, "w") as f:
                f.write(vbs)
            try:
                result = subprocess.run(
                    ["cscript", "//Nologo", vbs_path],
                    capture_output=True, text=True, timeout=180,
                )
                chosen = (result.stdout or "").strip()
                print(f"[browse] vbscript returned rc={result.returncode} path={chosen!r}", flush=True)
                if chosen and chosen != "::CANCELLED::":
                    return jsonify({"ok": True, "path": chosen})
                if chosen == "::CANCELLED::":
                    return jsonify({"ok": False, "cancelled": True})
                return jsonify({
                    "ok": False,
                    "error": "Couldn't open the folder picker on this machine. Please type or paste the folder path directly into the box instead.",
                })
            finally:
                try:
                    os.remove(vbs_path)
                except OSError:
                    pass

        else:
            # macOS: osascript opens a native Finder folder picker.
            result = subprocess.run(
                ["osascript", "-e", 'POSIX path of (choose folder with prompt "Choose a folder")'],
                capture_output=True, text=True, timeout=120,
            )
            chosen = result.stdout.strip().rstrip("/")
            if result.returncode != 0 or not chosen:
                # User cancelled (osascript returns non-zero on cancel)
                return jsonify({"ok": False, "cancelled": True})
            return jsonify({"ok": True, "path": chosen})

    except subprocess.TimeoutExpired:
        return jsonify({"ok": False, "cancelled": True})
    except Exception as e:
        return jsonify({"ok": False, "error": f"Folder picker error: {e}"})


@app.route("/autostart-preview", methods=["GET"])
def autostart_preview():
    """Returns a preview of what auto-start registration will do."""
    return jsonify(_autostart_preview())


@app.route("/autostart-register", methods=["POST"])
def autostart_register():
    """Actually registers the auto-start task."""
    ok, message = _register_autostart()
    return jsonify({"ok": ok, "message": message})


def _run_install(which, packages):
    """
    Runs in a background thread so the page stays responsive while pip
    does its (sometimes slow) thing.
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
    """Opens the setup page automatically a moment after the server starts."""
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
