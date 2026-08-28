#!/bin/bash
# start_setup.command
#
# Double-click this file (in Finder) to set up the journal RAG app. It
# checks whether Python is installed, helps you get it if it's not, and
# then launches the setup wizard in your browser. This is the ONLY thing
# you should need to double-click to get started -- everything else
# happens inside the wizard page itself.
#
# If double-clicking this shows a security warning instead of running it
# (common the very first time you run a downloaded script on a Mac):
# right-click (or Control-click) the file, choose "Open", then click
# "Open" again in the dialog that appears. You only need to do that once.

# cd to the folder this script actually lives in, no matter where it was
# double-clicked from or what the Terminal's starting folder happens to
# be -- otherwise setup_wizard.py might not be found.
cd "$(dirname "$0")" || {
    echo "Couldn't find this script's own folder -- something unusual is"
    echo "going on with how it was launched. Press Return to close this window."
    read -r
    exit 1
}

echo ""
echo "  Journal RAG - Setup"
echo "  ==================="
echo ""

# --- Find a working Python 3 ---
# Modern Macs ship "python3" but often do NOT have a plain "python" on
# PATH (or if they do, it can be an old Python 2 left over from the OS
# itself) -- so python3 is checked first and preferred throughout.
#
# Being findable on PATH isn't quite the same as actually working --
# e.g. a stale symlink, a half-finished Homebrew install, or a broken
# pyenv shim can all show up in `command -v` but fail the moment they're
# actually run -- so both branches below confirm `--version` genuinely
# succeeds before trusting that command, not just that it exists.
PYTHON_CMD=""
if command -v python3 >/dev/null 2>&1 && python3 --version >/dev/null 2>&1; then
    PYTHON_CMD="python3"
elif command -v python >/dev/null 2>&1; then
    # Only fall back to a bare "python" if it's genuinely Python 3 --
    # never silently run this project under Python 2, which large parts
    # of it wouldn't work correctly under anyway.
    VERSION_OUTPUT="$(python --version 2>&1)"
    if [[ "$VERSION_OUTPUT" == *"Python 3"* ]]; then
        PYTHON_CMD="python"
    fi
fi

if [ -z "$PYTHON_CMD" ]; then
    echo "  Python 3 wasn't found on this computer."
    echo ""
    echo "  This app needs Python to run (it's free, and only takes a"
    echo "  couple of minutes to install). Opening the download page for"
    echo "  you now -- grab the macOS installer, run it, then come back"
    echo "  and double-click this file again."
    echo ""
    open "https://www.python.org/downloads/"
    echo "  Press Return to close this window."
    read -r
    exit 1
fi

echo "  Found Python ($PYTHON_CMD). Starting the setup wizard..."
echo ""
echo "  Your browser should open automatically in a moment."
echo "  If it doesn't, go to: http://localhost:5050"
echo ""
echo "  Leave this window open while you use the setup page."
echo "  Closing this window will stop the setup wizard."
echo ""

"$PYTHON_CMD" setup_wizard.py

echo ""
echo "  Press Return to close this window."
read -r
