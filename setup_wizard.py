"""
setup_wizard.py

A local web form for configuring this app without editing any code --
run it once when you first set the project up, or again anytime you want
to change your API key or export folder.

Run it:
    python setup_wizard.py

Then open http://localhost:5050 in your browser, fill in the fields, and
save. It writes a local `.env` file that config.py reads automatically
the next time you run ingest.py, ask.py, tag_backfill.py, or the server.

This page only listens on 127.0.0.1 (your own computer) -- unlike the
main chat app, it is never reachable from your phone or any other
device, which matters since this is the one page that handles your raw
API key. Nothing you type here is sent anywhere except into that local
.env file.
"""

import os

from flask import Flask, request

app = Flask(__name__)

_PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(_PROJECT_ROOT, ".env")


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


def render_page(banner_html, api_key, export_dir):
    page = _PAGE_TEMPLATE
    page = page.replace("__BANNER__", banner_html)
    page = page.replace("__API_KEY__", api_key)
    page = page.replace("__EXPORT_DIR__", export_dir)
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
    padding: 0 20px;
    line-height: 1.5;
  }
  h1 { font-size: 22px; }
  p.sub { color: #4A5568; font-size: 14px; margin-top: -8px; }
  label { display: block; font-weight: 600; margin-top: 20px; font-size: 14px; }
  .hint { font-weight: 400; color: #8B8478; font-size: 12.5px; margin-top: 2px; }
  input {
    width: 100%;
    box-sizing: border-box;
    padding: 10px 12px;
    margin-top: 6px;
    border: 1px solid #E8E1D2;
    border-radius: 6px;
    font-size: 14px;
  }
  button {
    margin-top: 26px;
    background: #5E7052;
    color: white;
    border: none;
    padding: 11px 22px;
    border-radius: 6px;
    font-size: 15px;
    cursor: pointer;
  }
  button:hover { background: #7C9070; }
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
</style>
</head>
<body>
  <h1>Set up your journal RAG app</h1>
  <p class="sub">Saved to a local <code>.env</code> file in this folder -- nothing leaves your computer.</p>
  __BANNER__
  <form method="POST" action="/save">
    <label>Anthropic API key
      <div class="hint">From console.anthropic.com &rarr; Settings &rarr; API Keys. Required.</div>
    </label>
    <input type="password" name="ANTHROPIC_API_KEY" value="__API_KEY__" placeholder="sk-ant-..." required>

    <label>Journal export folder
      <div class="hint">Where your Day One export file(s) land. Leave as-is unless you moved it.</div>
    </label>
    <input type="text" name="JOURNAL_EXPORT_DIR" value="__EXPORT_DIR__" placeholder="./exports">

    <button type="submit">Save</button>
  </form>
</body>
</html>
"""


@app.route("/", methods=["GET"])
def index():
    existing = read_existing_env()
    banner = ""
    if existing.get("ANTHROPIC_API_KEY"):
        banner = '<div class="banner">Already configured. Change anything below and save again to update it.</div>'
    return render_page(
        banner,
        existing.get("ANTHROPIC_API_KEY", ""),
        existing.get("JOURNAL_EXPORT_DIR", "./exports"),
    )


@app.route("/save", methods=["POST"])
def save():
    api_key = (request.form.get("ANTHROPIC_API_KEY") or "").strip()
    export_dir = (request.form.get("JOURNAL_EXPORT_DIR") or "./exports").strip()

    if len(api_key) < 20:
        banner = ('<div class="banner warn">That doesn&#39;t look like a full API key -- '
                   'please paste the whole thing from console.anthropic.com.</div>')
        return render_page(banner, "", export_dir)

    values = read_existing_env()
    values["ANTHROPIC_API_KEY"] = api_key
    values["JOURNAL_EXPORT_DIR"] = export_dir
    write_env(values)

    banner = ('<div class="banner success">Saved! Next: close this tab, then in your terminal run '
               '<code>python ingest.py</code>, and once that finishes, '
               '<code>python webapp/server.py</code>.</div>')
    return render_page(banner, api_key, export_dir)


if __name__ == "__main__":
    print("\nOpen this in your browser to finish setup: http://localhost:5050\n")
    app.run(host="127.0.0.1", port=5050, debug=False)
