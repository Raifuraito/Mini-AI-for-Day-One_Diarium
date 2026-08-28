# Security policy

This is a personal, local-first journal tool. There's no company or team
behind it, but here's how it protects your data and what to watch out for
if you run it yourself.

## How your data is handled

- Your journal never leaves your computer, except for the specific
  excerpts relevant to a question, which get sent to Anthropic's API in
  order to generate an answer. That's true whether you're asking a
  question or letting the automatic tagging step run at ingest time.
- The vector database (`chroma_db/`), extracted photos (`photos/`), your
  raw exports (`exports/`, `_extracted_exports/`), and the chat log
  (`chat_history.jsonl`) all live only on your machine and are excluded
  from git via `.gitignore`. If you're setting this up from a cloned copy
  of someone else's repo, you're starting with none of that -- it gets
  created fresh the first time you run `ingest.py`.

## Your API key

- Set it via `setup_wizard.py` (writes a local `.env` file) or your
  shell's environment variables (`export`/`setx`) -- never hardcoded
  anywhere in the code.
- `.env` is already in `.gitignore`. Double-check `git status` before
  your first commit if you're not sure it's being respected.
- If a key is ever accidentally committed or exposed, revoke it
  immediately at [console.anthropic.com](https://console.anthropic.com)
  and issue a new one -- there's no way to "undo" an exposed key otherwise.

## Network exposure

- `webapp/server.py` binds to `0.0.0.0:5000` on purpose, so it's reachable
  from your phone over Tailscale. **There is no login or password on this
  app** -- anyone who can reach that port can read your journal and ask it
  questions.
  - Fine: using it over [Tailscale](https://tailscale.com) (a private
    mesh network between your own devices).
  - Risky: leaving it running on a shared/public Wi-Fi network without
    Tailscale, where others on the same network may be able to reach
    port 5000.
  - Don't: port-forward port 5000 on your router to expose it to the
    open internet. This app was not built with that threat model in mind.
- `setup_wizard.py` (the config UI) binds to `127.0.0.1` only -- it's
  never reachable from another device, even over Tailscale. That's
  intentional, since it's the one page that handles your raw API key.

## Reporting a problem

This is a personal/hobby project without a formal disclosure process.
If you find a security issue, the simplest path is to open a GitHub
issue (avoid including real journal content or API keys in it) or reach
out to the repo owner directly.
