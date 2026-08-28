# Ask Your Journal

A local RAG (retrieval-augmented generation) pipeline that lets you ask questions about your Day One journal in plain English. Runs entirely on your own machine using the Anthropic API — no data leaves your computer except the specific journal excerpts sent per question.

## What it does

- Ask natural questions: *"What was I feeling last spring?"* or *"Detail my trip to California"*
- Searches journal text **and** photos together (Phase 5 ambient visual search)
- Finds photos by visual content using CLIP — *"show me the beach photo"* works even if you never wrote the word "beach"
- Smart question routing: trend questions, synthesis/action-item questions, date-range questions, and photo searches all use different retrieval strategies
- **Max Recall mode**: a ⚡ toggle in the chat UI that guarantees completeness for "everything about X" questions — instead of just the top handful of semantically similar entries, it pulls *every* entry tagged with that topic. Costs a bit more per question (more context = more tokens) but is built for the questions where missing an entry actually matters
- Conversation memory: *"tell me more about that"* works across turns
- Mood chart: monthly sentiment scores from your entries
- Phone-friendly web app, installable to your home screen via Tailscale

## Setup

### 1. Install dependencies

```bash
pip install chromadb anthropic flask
# Optional: for visual/photo search (Phase 4+)
pip install open-clip-torch pillow
```

### 2. Get an Anthropic API key

Sign up at [console.anthropic.com](https://console.anthropic.com), create a key, and add a small amount of credit (a few dollars covers a long time at personal-use volume — this is separate from any Claude.ai subscription, it's pay-per-use API billing).

### 3. Configure the app

**Easiest path — no code editing:**

```bash
python setup_wizard.py
```

Open [http://localhost:5050](http://localhost:5050), paste in your API key and (optionally) your export folder, and save. It writes a local `.env` file that everything else in this project reads automatically. This page only listens on your own computer (`127.0.0.1`) — it's never reachable from your phone or anywhere else on the network.

**Or configure manually**, by setting environment variables yourself:

```bash
# Mac/Linux
export ANTHROPIC_API_KEY="your-key-here"

# Windows
setx ANTHROPIC_API_KEY "your-key-here"
# then reopen the terminal
```

Either approach works, and you can mix them — a real environment variable always takes priority over `.env`, so switching between the two later is safe.

### 4. Export your journal

**On your computer**: Day One → Journal Settings → Export Journal → JSON (include media if you want photo search). Save it to a folder you'll remember — see the tip below about using a synced folder if you also journal from your phone.

**On your phone (iOS/Android)**: Day One → Settings → Import/Export → Export → JSON. Wait for the export notification, then Share → Save to Files (or your synced folder's app).

**Tip — one folder for both**: if you use Day One on both your phone and computer, pick one cloud-synced folder (iCloud Drive, Dropbox, Google Drive — whatever you already use) as your drop point for exports, and point `JOURNAL_EXPORT_DIR` (or the setup wizard's export folder field) at its location on your computer. That one folder becomes the single hand-off point between Day One and this project — export from either device, and it lands in the same place automatically.

Day One doesn't support exporting just the entries written since your last export — only the whole journal at once. That's fine here: `ingest.py` hashes each entry and only re-embeds (and re-pays-for) ones that are new or changed, so re-exporting and re-running ingestion regularly is cheap and safe, never a full re-processing.

### 5. Ingest your journal

```bash
python ingest.py path/to/your_export.json
```

This embeds your entries into a local vector database (`chroma_db/`) and extracts tags (people/places/themes) for each one, which is what powers Max Recall. Only new or changed entries are re-embedded on subsequent runs.

To force a full re-embed (e.g. after updating `ingest.py`):
```bash
python ingest.py path/to/your_export.json --force
```

### 6. Ask questions (command line)

```bash
python ask.py "What was going on with me last spring?"
python ask.py "Detail my trip to California"
python ask.py "What have I not reflected on in a while?"
```

### 7. Start the web app

```bash
cd webapp
python server.py
```

Open [http://localhost:5000](http://localhost:5000) in your browser.

## Phone access via Tailscale

1. Install [Tailscale](https://tailscale.com/download) on your computer and phone
2. Sign in with the same account on both
3. On your phone, open `http://<your-computer-tailscale-name>:5000`
4. Tap Share → Add to Home Screen for a full-screen app icon

## Automating ingestion

**Mac/Linux (cron):**
```bash
crontab -e
# Add:
0 8 * * * cd /path/to/journal-rag && python3 ingest.py >> ingest.log 2>&1
```

**Windows (Task Scheduler):**
- Create Basic Task → Daily → run `python.exe` with argument `ingest.py`, Start In set to your project folder

Once scheduled, this needs no manual attention — it quietly skips unchanged files and only processes what's new.

## Tagging and Max Recall

Every entry gets a short list of tags (notable people, places, themes) extracted automatically the first time it's ingested, using `TAG_EXTRACTION_MODEL` (Haiku by default — a cheaper, faster model than the one used for Q&A, since tagging doesn't need top-tier quality). These tags are what let Max Recall guarantee completeness: asking "everything about my trip to California" with Max Recall on retrieves *every* entry tagged with that topic, not just the ones that happen to rank highest in a similarity search.

**If some entries are missing tags** — because they were ingested before tagging existed, or a large first-time ingest got interrupted — catch them up with:

```bash
python tag_backfill.py --count        # see how many entries still need tags (free, no API calls)
python tag_backfill.py                # tag up to 300 of them, then stop
python tag_backfill.py --limit 500    # tag up to 500 this run
python tag_backfill.py --all          # tag everything that's left, in one go
```

It's safe to interrupt (Ctrl+C) and safe to re-run — every batch is saved as it completes, and an entry Claude finds nothing notable in is remembered so it's never re-sent (and re-paid-for) on a later run.

## Configuration

Edit `config.py` (or use `setup_wizard.py` for the basics) to adjust:

| Setting | Default | Description |
|---------|---------|-------------|
| `MAX_CONTEXT_CHUNKS` | 8 | Chunks per regular question |
| `TREND_MAX_CONTEXT_CHUNKS` | 25 | Chunks for trend/pattern questions |
| `SYNTHESIS_MAX_CONTEXT_CHUNKS` | 50 | Chunks for synthesis/action-item questions |
| `MAX_POWER_CONTEXT_CHUNKS` | 200 | Ceiling for Max Recall's tag-matched retrieval |
| `PHASE5_MAX_VISUAL_RESULTS` | 1 | Max ambient photos per non-photo question |
| `PHASE5_VISUAL_THRESHOLD` | 0.30 | CLIP similarity cutoff for ambient photos |
| `DIVERSITY_ENTRY_THRESHOLD` | 3 | When to apply diversity sampling |
| `TAG_EXTRACTION_MODEL` | `claude-haiku-4-5` | Model used to tag entries at ingest time |
| `TAG_BATCH_SIZE` | 15 | Entries tagged per API call (fewer round trips) |

## Project structure

```
journal-rag/
├── ask.py                 # Question routing + retrieval logic
├── ingest.py               # Embeds journal entries into ChromaDB, extracts tags
├── tag_backfill.py         # Catches up entries missing tags, in resumable batches
├── setup_wizard.py         # Local web form for API key + path configuration
├── config.py               # All settings in one place
├── image_embed.py          # CLIP-based photo embeddings (Phase 4+)
├── watcher.py               # Optional: auto-start server when Chrome opens
├── webapp/
│   ├── server.py           # Flask web server
│   └── templates/
│       └── index.html      # Chat UI
├── .env                     # Your API key + paths (created by setup_wizard.py, gitignored)
├── .gitignore               # Keeps your journal data and secrets out of git
├── LICENSE                  # MIT
└── SECURITY.md              # How your data and API key are handled
```

## Cost

- **Embeddings**: free (local model, no API calls)
- **Ingestion**: free unless you set `USE_HOSTED_EMBEDDINGS = True`
- **Tagging**: a small fraction of a cent per entry with the default Haiku model — see `tag_backfill.py` above for catching up existing entries
- **Questions**: ~$0.001–0.004 per question via the Anthropic API (scales with context chunks, not journal size; Max Recall questions cost more since they pull more context on purpose)

## Privacy & security

Your journal stays on your machine. Only the specific excerpts relevant to each question (or entry, for tagging) are sent to the Anthropic API. The `chroma_db/`, `photos/`, export folders, chat history, and your `.env` file are all excluded from git via `.gitignore`. See [SECURITY.md](SECURITY.md) for details on API key handling and safe network exposure (e.g. why the web app binds to `0.0.0.0` and what that means if you use it without Tailscale).

## License

[MIT](LICENSE) — do what you like with it.
