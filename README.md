# Ask Your Journal

A private, local AI assistant for your journal. Ask it questions in plain English -- *"What was I feeling last spring?"*, *"Detail my trip to California"*, *"What have I not reflected on in a while?"* -- and it searches your actual entries (and photos) to answer, the same way you'd ask a friend who'd read the whole thing.

It runs entirely on your own computer. Your journal itself never leaves your machine -- only the small handful of entries relevant to whatever you just asked get sent out, to generate that one answer.

This guide assumes no prior context. If you've never written a line of code, you can still get this running by following it top to bottom.

## Table of contents

- [Is this for you?](#is-this-for-you)
  - [If you journal on your phone: which combo is easiest?](#if-you-journal-on-your-phone-which-combo-is-easiest)
- [What this actually costs](#what-this-actually-costs)
- [Before you start: what you'll need](#before-you-start-what-youll-need)
- [Step 1: Download and open the folder](#step-1-download-and-open-the-folder)
- [Step 2: Run the setup wizard](#step-2-run-the-setup-wizard)
- [Step 3: Export your journal](#step-3-export-your-journal)
  - [Exporting from a computer](#exporting-from-a-computer)
  - [Exporting from a phone (iOS/Android)](#exporting-from-a-phone-iosandroid)
  - [Keeping phone and computer in sync automatically](#keeping-phone-and-computer-in-sync-automatically)
- [Step 4: Bring your journal in](#step-4-bring-your-journal-in)
- [Step 5: Start asking questions](#step-5-start-asking-questions)
- [Using it from your phone](#using-it-from-your-phone)
- [Keeping it up to date automatically](#keeping-it-up-to-date-automatically)
- [Tags and Max Recall, explained](#tags-and-max-recall-explained)
- [Photo search (optional)](#photo-search-optional)
- [All the settings, explained](#all-the-settings-explained)
- [Known limitations, please read this section](#known-limitations-please-read-this-section)
- [Privacy and security](#privacy-and-security)
- [If something goes wrong](#if-something-goes-wrong)
- [Project structure](#project-structure)
- [License](#license)

## Is this for you?

This project exists as a free, self-hosted alternative to paying for Day One's built-in AI features (or a similar paid journaling-AI product). If you already journal in **Day One**, this is close to a drop-in addition -- export your journal, point this at the export, and start asking it questions, at whatever the API costs actually are (typically a few cents to a few dollars a month with a hosted provider, or nothing at all with a local model -- see below) instead of a subscription.

It also has **best-effort, not-fully-verified** support for **Diarium** exports, and a generic fallback that has a reasonable chance of working with other journaling apps that export to JSON. See [Known limitations](#known-limitations-please-read-this-section) for exactly what "best-effort" means here before you rely on it.

If you don't journal digitally in a format this can read, this project won't be useful to you as-is.

### If you journal on your phone: which combo is easiest?

**If you use Day One:** what actually determines the effort here is which *phone* you have, not which computer you pair it with. Day One's own real-time Sync between devices is a paid Silver/Gold feature ($49.99-$74.99/year) -- if you're using a free, self-hosted tool specifically to avoid subscription costs, you're probably not also paying for that, which means your phone is the one place your entries and photos actually live, and getting a fresh export means going back to the phone every time, regardless of which desktop you're pairing it with.

| | iPhone | Android |
|---|---|---|
| Getting the export into your synced folder | A one-time ~2-minute [Apple Shortcut](#getting-exports-there-without-touching-a-cable) bridges Day One's share sheet into it | The share sheet already targets Dropbox/Drive directly -- no bridge needed |
| Ongoing effort, once that's set up | One tap | One tap |
| Changes with Windows vs. Mac? | No -- both run every major cloud provider's desktop app identically | No |

Windows+iPhone and Mac+iPhone are identical to each other here, and Windows+Android and Mac+Android are identical to each other -- the desktop half of the pairing doesn't actually change anything. If you're on Android, you're already in the easier spot; if you're on iPhone, the Shortcut above is genuinely a one-time cost, not an ongoing one.

**If you use Diarium:** the friction isn't about exporting at all -- Diarium already syncs continuously through your own cloud folder, so there's no phone-to-computer file dance the way there is with Day One. The catch is that Cloud Sync is a separate paid upgrade *on each platform* (the Windows app ships with it included; iPhone, Android, and Mac each need their own purchase to unlock it), and Google Drive specifically is the slowest of Diarium's supported cloud backends, if you have a choice of provider.

## What this actually costs

**We don't take a cut.** This project is free and open-source -- the only cost is what the AI provider you choose charges you directly for their API, and if you're running a local model (Ollama, LM Studio, etc.), that cost is zero beyond your own electricity. Running a local model privately is only recommended if you have enough RAM to host one (usually 16 GB+).

Nothing here requires a subscription. This project works with several AI providers now (Anthropic, OpenAI, Google, Mistral, or a local model -- pick one in the setup wizard's Step 2), and what it costs is small, pay-as-you-go usage of whichever one you choose.

- **Setting up and running the app itself**: free. Flask, the vector database, and the web page all run locally with no cost.
- **Embedding your journal into the local database**: free. This uses a local model, not an API call.
- **Extracting tags** (what powers Max Recall, see below) -- **entirely optional, off by default**: a small amount per entry, using the cheapest model for whichever provider you picked. See the reference table just below for what tagging a first-time journal of about 1,000 entries actually costs by provider -- well under a dollar either way, but the exact number varies more than you'd guess between providers.
- **Asking a question**: cost scales with how much context a question needs, not with how big your journal is -- a normal question and a "give me everything about X" Max Recall question cost differently, but neither gets more expensive just because you've been journaling for ten years instead of one. Ballpark: a few tenths of a cent per question with OpenAI, Google, or Mistral's cheap-tier models; roughly $0.005-$0.02 per question with Claude (Haiku or Sonnet, depending which you picked); nothing with a local model beyond your own electricity.

Realistically, for one person's personal use, this comes out to somewhere between a few cents and a few dollars a month depending on how often you use it and which provider you picked. Every hosted provider needs a small amount of prepaid API credit -- there's no free tier that avoids this step, but there's also no minimum spend or subscription; you're billed only for what you actually use. Every dollar goes to that provider, not to us.

### Reference: tagging a 1,000-entry journal from scratch

A concrete "how much will this actually cost me" number, instead of a vague "fraction of a cent" -- this is what a **first-time tagging backfill of about 1,000 journal entries** costs, by provider:

**Budget tier** (what tagging uses by default -- the cheapest model from each provider):

| Provider & model | Input | Output | ~1,000 entries |
|---|---|---|---|
| OpenAI (`gpt-4o-mini`) | $0.15 / M tokens | $0.60 / M tokens | **~$0.05** |
| Google (`gemini-2.0-flash`) | $0.15 / M tokens | $0.60 / M tokens | **~$0.05** |
| Mistral (`mistral-small`) | $0.15 / M tokens | $0.60 / M tokens | **~$0.05** |
| Anthropic (`claude-haiku-4-5`) | $1.00 / M tokens | $5.00 / M tokens | **~$0.39** |

**Chat tier** (what answering questions uses -- the model you actually pick in the dropdown):

| Provider & model | Input | Output | ~1,000 entries |
|---|---|---|---|
| OpenAI (`gpt-4.1-mini`) | $0.40 / M tokens | $1.60 / M tokens | **~$0.14** |
| Google (`gemini-2.5-pro`) | $1.25 / M tokens | $10.00 / M tokens | **~$0.61** |
| Mistral (`mistral-medium`) | $0.40 / M tokens | $2.00 / M tokens | **~$0.15** |
| Anthropic (`claude-sonnet-4-5`) | $3.00 / M tokens | $15.00 / M tokens | **~$1.16** |

**Local (free)**:

| Provider | Input | Output | ~1,000 entries |
|---|---|---|---|
| Ollama / LM Studio / other local | $0 | $0 | **$0** |

Token pricing above is current as of August 2026 (Anthropic's own pricing docs; independent pricing trackers for the others) -- check your provider's pricing page before budgeting against this, since rates do change over time.

Worth knowing: Claude Haiku is Anthropic's *cheapest* model, but it's still roughly 7-8x pricier per token than the cheap tier from OpenAI, Google, or Mistral. So if minimizing tagging cost specifically is your priority, Haiku isn't the cheapest option overall -- just the cheapest *Anthropic* one. All of these numbers are small enough (under a dollar for 1,000 entries either way) that this mostly matters if you're tagging a much larger journal, or you're just curious.

**How this is estimated**, so you can recompute it for your own journal size or if pricing changes: tagging batches `TAG_BATCH_SIZE` entries (15 by default) per API call, with each entry's text capped at `TAG_SNIPPET_CHARS` (800 characters) plus a short fixed instruction prompt. That works out to roughly 3,300 input tokens and 500 output tokens per batch of 15 entries -- about 67 batches for 1,000 entries, totaling roughly 220,000 input tokens and 33,000 output tokens. Multiply those by whichever provider's per-million-token rate above to get the total. Real cost is usually a bit lower than this estimate, since it assumes every entry uses the full 800-character cap -- shorter entries cost less.

## Before you start: what you'll need

- **A computer** (Windows or Mac) to run this on. It stays running only when you want to use it -- it's not a background service you have to leave on all the time (see [Keeping it up to date automatically](#keeping-it-up-to-date-automatically) if you do want that).
- **A journal export** from Day One (or Diarium, with caveats) -- a JSON file. If you haven't exported one yet, that's covered in [Step 3](#step-3-export-your-journal) below.
- **An Anthropic API key** -- a password-like string that lets this project make API calls on your behalf, billed to you directly. Getting one is covered in [Step 2](#step-2-run-the-setup-wizard) below; it takes about two minutes.
- Optionally, if you want to use this from your phone too: **[Tailscale](https://tailscale.com)**, a free app that creates a private network between just your own devices. Covered in [Using it from your phone](#using-it-from-your-phone).

You do **not** need to already know Python, or how to use a terminal, to get through setup -- the steps below are written assuming you don't.

## Step 1: Download and open the folder

Download this project (from GitHub: the green "Code" button, then "Download ZIP"; or however you received it), and unzip it somewhere you'll remember -- your Documents folder is a fine choice.

Open that unzipped folder. You should see a file called **`start_setup.bat`** (Windows) or **`start_setup.command`** (Mac) sitting near the top, alongside files like `README.md` (this file) and `setup_wizard.py`.

## Step 2: Run the setup wizard

**Double-click `start_setup.bat`** (Windows) or **`start_setup.command`** (Mac).

> **On a Mac**, the very first time you run it, you may see a security warning instead of it just running -- this is normal for any downloaded script, not specific to this project. Right-click (or Control-click) the file, choose **Open**, then click **Open** again in the dialog that appears. You only need to do this once; after that, double-clicking works normally.

This opens a small black terminal window. Leave it open -- closing it stops the setup page. A couple of things happen automatically:

- If Python isn't installed on your computer yet, it tells you and opens the download page for you. Install Python (on the very first screen of the Windows installer, **check the box that says "Add python.exe to PATH"** before clicking Install -- this step is easy to miss and matters), then double-click the launcher file again.
- If Python is already there, it starts the setup page and opens it in your browser automatically, at `http://localhost:5050`. If your browser doesn't open on its own, just go to that address yourself.

On that page:

1. **(Optional) Click "Install packages."** This installs the two things the app needs (`chromadb` and `anthropic`) for you, with a live progress log right on the page. You can skip this if you'd rather install them yourself, or already have.
2. **Get an API key.** Go to [console.anthropic.com](https://console.anthropic.com), sign up or sign in, and create a new API key (usually under Settings → API Keys). Add a small amount of credit to the account -- a few dollars covers a long time at personal-use volume (see [What this actually costs](#what-this-actually-costs) above). This is separate from any Claude.ai subscription; it's its own pay-per-use billing.
3. **Paste that key into the "Anthropic API key" field** on the setup page.
4. **Decide about tagging.** There's a checkbox for this, unchecked by default, with real cost numbers right next to it -- see [Tags and Max Recall, explained](#tags-and-max-recall-explained) below for what it does and why it's opt-in rather than automatic. Leaving it unchecked is completely fine; you can always turn it on later from this same page.
5. **Set your journal export folder.** This is where you'll drop your export file(s) -- see [Step 3](#step-3-export-your-journal). The default (`./exports`, a folder right inside this project) is fine for most people. If that folder doesn't exist yet, click **"Create this folder"** right next to the field and it'll be made for you.
6. Leave the "Advanced" section closed unless you have a specific reason to change where the database or tracking files live -- the defaults are fine.
7. **Click Save.**

That's it for this page. It writes everything into a file called `.env` inside this project folder -- more on exactly what that means and why it's safe in [Privacy and security](#privacy-and-security).

## Step 3: Export your journal

### Exporting from a computer

**Day One (Mac/Windows app):** Journal Settings → Export Journal → choose **JSON** (check "include media" if you want photo search) → save the file into the export folder you set up in Step 2.

**Diarium:** use Diarium's own JSON export option from its settings/backup menu, and save it into the same folder. See the [Known limitations](#known-limitations-please-read-this-section) section for what "best-effort support" means for Diarium specifically, before relying on this.

### Exporting from a phone (iOS/Android)

**Day One app:**

1. Open Day One on your phone.
2. Go to **Settings**.
3. Tap **Import/Export**.
4. Tap **Export**, then choose **JSON**.
5. Wait for the export to finish -- Day One will show a notification when it's ready.
6. Tap **Share**, then choose where to send it. If you've set up the synced-folder approach below, save it there directly; otherwise, save it to Files (iOS) or your Downloads (Android) and move it to your computer's export folder afterward (e.g. by AirDrop, email to yourself, or a cloud drive app).

**Diarium app:** the exact menu wording varies by version, but look for an **Export** or **Backup** option in Diarium's settings, and choose the JSON format if offered (Diarium's primary backup format is a proprietary `.diary` file, not JSON -- make sure you're specifically choosing a JSON export, not the regular backup, since only JSON is what this project can read).

### Keeping phone and computer in sync automatically

If you journal from both your phone and your computer, exporting and manually moving the file every time gets old fast. The fix: pick **one cloud-synced folder** you already use anyway -- iCloud Drive, Dropbox, Google Drive, whatever -- and point the wizard's **"Sync folder (drop zone)"** field at it. This is separate from the **"Storage folder (local)"** field, which is where exports actually live long-term -- the sync folder is just a temporary channel: `watcher.py` copies anything new out of it into permanent storage and deletes it from the synced copy, so your cloud storage never fills up with old exports.

Once that's set up, exporting from either device (following the steps above, just choosing that synced folder as the save location) lands the file in permanent storage automatically within a minute or so, with no extra manual step to move it around.

Day One doesn't support exporting just the entries since your last export -- only the whole journal, every time. That's fine here on purpose: this project remembers which entries it's already processed (by a checksum of their content, not by trusting file names or dates), so re-exporting your whole journal and re-running ingestion regularly is cheap and safe. Only entries that are actually new or edited get sent to the API and re-processed; nothing gets double-charged.

## Step 4: Bring your journal in

Once you've saved an export file into your export folder (Step 3), open a terminal in this project's folder and run:

```bash
python ingest.py
```

With no file name given, this automatically finds and processes every `.json`/`.zip` export sitting in your configured export folder -- which is exactly what you'll have after Step 3. (If you'd rather point it at one specific file directly, `python ingest.py path/to/your_export.json` works too.)

This reads your entries, splits them into a local database, and extracts a short list of tags (people, places, themes) for each one -- which is what powers the "Max Recall" feature explained below. The very first run, on a full journal, can take a little while depending on how many entries you have; every run after that is much faster, since only new or changed entries get reprocessed.

**Not sure how to open a terminal in this folder?** On Windows, open the project folder in File Explorer, click into the address bar, type `cmd`, and press Enter. On Mac, open the project folder in Finder, then right-click (or Control-click) an empty spot inside it and choose "New Terminal at Folder" (or open Terminal normally and type `cd ` followed by dragging the folder in, then press Enter).

## Step 5: Start asking questions

You have two ways to ask questions: a full chat web page, or quick one-off questions from the terminal.

**The web app (recommended):**

```bash
cd webapp
python server.py
```

Then open [http://localhost:5000](http://localhost:5000) in your browser. This is a proper chat interface, with conversation memory (you can say "tell me more about that" and it'll understand what "that" refers to), a mood chart, photo results, and the Max Recall toggle.

**From the terminal, for a quick one-off question:**

```bash
python ask.py "What was going on with me last spring?"
python ask.py "Detail my trip to California"
```

## Using it from your phone

The web app above only listens on your own computer by default. To reach it from your phone too, without exposing it to the whole internet:

1. Install [Tailscale](https://tailscale.com/download) on your computer and on your phone.
2. Sign in with the same account on both.
3. Make sure `python server.py` (from Step 5) is running on your computer.
4. On your phone, open `http://<your-computer-tailscale-name>:5000` in a browser (Tailscale's app shows you this name).
5. Tap Share → Add to Home Screen for a full-screen app icon, so it behaves like a normal app from then on.

**Important:** there is no login or password on this app. Tailscale is what keeps it private -- it only makes the app reachable to your own signed-in devices, not the open internet. See [Privacy and security](#privacy-and-security) for what this means if you ever consider exposing it any other way (short version: don't port-forward it).

## Keeping it up to date automatically

**This is now automatic — you don't need to do anything below.** The setup wizard (`python setup_wizard.py`) registers `watcher.py` to start by itself whenever you log in, the moment you click Save. `watcher.py` checks for new exports every 60 seconds and starts the web server automatically whenever it notices Chrome is open, so there's genuinely nothing to run by hand from here on — no terminal window to leave open, no schedule to configure yourself. This happens on Windows (via Task Scheduler) and Mac (via a LaunchAgent); if you're on Linux, or if it ever reports that this step didn't finish, see the manual fallback further down.

### Getting exports there without touching a cable

The wizard's **"Sync folder (drop zone)"** field doesn't have to be a plain local folder — point it at a cloud-sync folder instead (Dropbox, iCloud Drive, OneDrive, Google Drive, Proton Drive, MEGA, or anything similar) and exporting from your phone into that same synced folder means it shows up here automatically within a minute, no manual copying required. The wizard shows every provider with a sensible default path already filled in, plus a Browse button next to each field if you'd rather point-and-click to the exact folder than type a path.

**Need to change this later?** Click the &#9881; Settings icon in the main journal app's header — it opens the wizard right where you left off, with your API key already filled in (you don't need to re-enter it unless you're actually replacing it).

**Day One on iPhone, via Apple Shortcuts:** Day One's own export can't write directly into an arbitrary folder, but a two-action Shortcut bridges that gap. In the Shortcuts app:

1. Create a new shortcut, set its type to accept files from the Share Sheet (Shortcut Details → "Show in Share Sheet," with Share Sheet Types set to "Files").
2. Add one action: **Save File** — when you run it, tap the folder icon and pick the *same* cloud-sync folder you set as your export folder above (this is a one-time choice; the Shortcut remembers it).
3. From Day One: Settings → Export → PDF/JSON → Share → find your Shortcut in the share sheet.

That's the whole Shortcut — two steps, no scripting. (There isn't a pre-built one-click file to import here: Apple's Shortcuts format doesn't have a reliable way to hand someone a working file sight-unseen, and testing one blind on hardware I don't have access to isn't something to guess at. Building it yourself in the app, from the three steps above, takes about the same two minutes either way.)

### Manual fallback

If the automatic step above ever reports it couldn't finish (the wizard's banner will say so directly, with the actual error), or you're on Linux/WSL where there's no equivalent, here's the manual version:

```bash
python watcher.py
```

Leave that running in a terminal window, and it checks for new exports every 60 seconds, ingesting anything new automatically.

**Mac (cron), ingest.py only:**
```bash
crontab -e
# Add this line, then save and close:
0 8 * * * cd /path/to/journal-rag && python3 ingest.py >> ingest.log 2>&1
```

**Windows (Task Scheduler), ingest.py only:** open Task Scheduler → Create Basic Task → Daily → have it run `python.exe` with the argument `ingest.py`, and set "Start In" to this project's folder.

**Windows (Task Scheduler), auto-starting `watcher.py` itself:** this is a *different* recipe from the one above -- that one schedules `ingest.py` alone, on a daily timer. `watcher.py` needs to start once and then keep running continuously, so it needs an **At log on** trigger, not Daily -- a Daily trigger will only ever fire once at a fixed clock time, which looks like "it just doesn't start" if you're not at your computer then.

1. Open Task Scheduler → Action → Create Task... (not "Create Basic Task" this time -- the basic wizard doesn't expose the trigger type this needs).
2. **General tab:** name it (e.g. "Journal-Rag Watcher"). Under "Security options," select "Run whether user is logged on or not" only if you want it fully invisible; "Run only when user is logged on" is simpler and fine for personal use.
3. **Triggers tab** → New... → "Begin the task" dropdown → **At log on** → OK.
4. **Actions tab** → New... → Program/script: the *full path* to your `python.exe` (not just `python` -- Task Scheduler doesn't always search the same PATH a regular terminal does). Not sure of that path? Open a terminal in this project's folder and run `where python` -- use the first line it prints. Add arguments: `watcher.py`. Start in: this project's folder (e.g. `C:\Projects\journal-rag`).
5. Save, entering your Windows password if prompted.

Test it with a log-off/log-on (or right-click the task → Run), then check Task Scheduler's "Last Run Result" column -- anything other than `(0x0)` or blank means it didn't actually start; the most common causes are exactly the two things step 4 calls out (a relative `python` instead of the full path, and the wrong trigger type).

## Tags and Max Recall, explained

**Tagging is off by default.** It's a genuinely useful feature, but it's also a real (if small) API cost per entry, and on a first-time ingest of a large journal that adds up before you've had a chance to decide you actually want it -- so instead of turning it on automatically, the setup wizard asks you directly, with real cost numbers, and you choose. Nothing else about the app depends on tagging being on: asking questions, embeddings, and the mood chart all work exactly the same either way.

**What tagging does, if you turn it on:** every entry gets a short list of tags -- notable people, places, and themes -- extracted the first time it's ingested, using a fast, inexpensive model (Haiku) separate from the one used to actually answer your questions.

**What tagging enables:** normal questions use semantic search, finding the handful of entries that seem most relevant to what you asked, ranked by similarity. That's fast and usually right, but for a question like *"tell me everything about my trip to California,"* "most similar" isn't the same as "complete" -- there could be a relevant entry from months later that doesn't rank in the top handful, even though it's clearly about the same trip. **Max Recall** (the ⚡ toggle in the chat UI) fixes that for topic questions specifically: instead of a similarity ranking, it pulls out **every single entry tagged with that topic**, guaranteeing nothing gets missed. It costs a bit more per question too, since more context means more tokens sent to the API -- but for the specific kind of question where missing an entry actually matters, that trade-off is the whole point. Max Recall simply has nothing to pull from if tagging has never run, so it's unavailable (not broken, just empty) until tagging is on and at least one ingest has happened with it enabled.

**Turning tagging on or off later:** re-run the setup wizard (`python setup_wizard.py`, or double-click the launcher) any time -- the checkbox reflects your current setting and changing it takes effect on your next `ingest.py` run. You can also flip it directly in `.env` (`ENABLE_TAGGING="true"` or `"false"`) or in `config.py` if you'd rather not use the wizard.

**If some entries are missing tags** -- because tagging was off when they were first ingested and you've since turned it on, or a large first-time tagging run got interrupted partway through -- catch them up with:

```bash
python tools/tag_backfill.py --count        # see how many entries still need tags (free, no API calls)
python tools/tag_backfill.py                # tag up to 300 of them, then stop
python tools/tag_backfill.py --limit 500    # tag up to 500 this run
python tools/tag_backfill.py --all          # tag everything that's left, in one go
```

It's safe to interrupt with Ctrl+C and safe to re-run -- progress is saved as each batch completes, and an entry Claude finds nothing notable in is remembered so it's never re-sent (and re-charged for) on a later run.

## Photo search (optional)

If you export your journal with photos included, this project can find them two ways: by which entry they're attached to (works automatically, no setup), and by **what's actually in the photo** using an image-recognition model called CLIP -- so asking "show me the beach photo" can work even if you never actually wrote the word "beach" anywhere in that entry.

The second part -- visual/content-based photo search -- is optional and needs an extra, fairly large install (`open-clip-torch` and `pillow`; the former pulls in PyTorch, which alone can be a few hundred MB). You can install it from the setup wizard page (check "Also set up photo search" before clicking install) or manually:

```bash
pip install open-clip-torch pillow
```

Everything else in the app works completely fine without this -- you just won't get photos showing up for a question that doesn't otherwise reference them by name.

## All the settings, explained

Most people never need to touch these -- the defaults are reasonable. If you want to tune things, edit `config.py` directly (a plain text file, safe to open in any text editor):

| Setting | Default | What it controls |
|---------|---------|-------------------|
| `MAX_CONTEXT_CHUNKS` | 8 | How many journal excerpts get pulled in per regular question. Higher = more thorough but more expensive. |
| `TREND_MAX_CONTEXT_CHUNKS` | 25 | Used automatically for pattern/trend questions ("how have I been feeling lately") -- these need more spread-out context to answer well. |
| `SYNTHESIS_MAX_CONTEXT_CHUNKS` | 50 | Used automatically for genuine synthesis questions ("summarize my meeting notes and give me action items"). |
| `MAX_POWER_CONTEXT_CHUNKS` | 200 | A ceiling (not a target) on how much Max Recall's complete-topic retrieval can pull in one go. |
| `TAG_EXTRACTION_MODEL` | Haiku | The model used to tag entries at ingest time. Cheap and fast on purpose -- point it at a different model if you want, but this doesn't need top-tier quality. |
| `TAG_BATCH_SIZE` | 15 | How many entries get tagged per API call. Fewer round trips this way, especially useful on a big first-time ingest. |

## Known limitations, please read this section

This project is built and maintained by one person for personal use, and shared as-is. Here's exactly where it's solid, and where it isn't, so there are no surprises:

- **Day One support is real and tested.** The JSON export format, the mobile export flow, and the whole pipeline have actually been built and run against real Day One exports.
- **Diarium support is best-effort and unverified.** Diarium has no published schema for its JSON export anywhere -- not in their own documentation, not on their forums, and a third-party project that reverse-engineered Diarium's separate `.diary` backup format doesn't cover this export either. Diarium's own developer has confirmed the app can't even re-import its own JSON export, meaning there isn't even a strict format being maintained internally to match against. The code tries several plausible field-name variations rather than betting on one single guess, and fails with a detailed, specific error (showing you exactly what shape it found) rather than silently producing wrong answers if none of those guesses fit your actual export. If it doesn't work with your export, please open a GitHub issue with a small, redacted sample of the JSON structure (field names and shape only -- leave out your actual journal content) so support can be added for real.
- **The generic fallback format** (for any other journal app that exports a flat list of date/text entries as JSON) is untested against any specific real app's export, for the same reason as Diarium -- there's no one format to test against. It should have a reasonable chance of working if your app's export is reasonably close to that shape, but "should" is doing real work in that sentence.
- **Mac compatibility has been code-reviewed and tested in a Mac-like (POSIX/Linux) environment, but not run on an actual Mac.** The one genuinely Windows-specific piece found in the whole codebase (how `watcher.py` checks whether Chrome is running) has been fixed to work on both. Everything else already worked identically on both operating systems by nature of how Python handles file paths. If something doesn't work as expected on a real Mac, please open a GitHub issue.
- **Photo search (the CLIP-based visual part) is a genuinely large optional install** -- see [Photo search](#photo-search-optional) above. Skip it if you don't need it; nothing else in the app is affected.
- **There is no login or password on the web app.** Anyone who can reach the port it's running on can read your journal and ask it questions. This is fine used privately over Tailscale (see [Using it from your phone](#using-it-from-your-phone)) and not fine exposed to the open internet -- see [Privacy and security](#privacy-and-security) below.
- **Real, small costs apply** via whichever AI provider you chose -- see [What this actually costs](#what-this-actually-costs) above for specifics. There's no way to use the tagging or question-answering features entirely for free with a hosted provider (Anthropic, OpenAI, Google, Mistral), since those require API calls. Running a local model (Ollama, LM Studio, etc.) avoids all API costs but needs enough RAM to host one.

## Privacy and security

Your journal stays on your computer. The only things that ever leave it are: the specific excerpts relevant to whatever question you just asked (sent to your chosen AI provider's API to generate the answer), and short snippets of each entry at tagging time (sent the same way, to extract that entry's tags). If you're using a local model (Ollama, LM Studio, etc.), nothing leaves your computer at all. Nothing else -- not your whole journal, not your photos in bulk, nothing -- is ever uploaded anywhere.

**Your API key** is either stored in a local `.env` file (created by the setup wizard) or in your own computer's environment variables -- never hardcoded anywhere in the code, and never sent anywhere except as authentication when this project itself calls your chosen provider's API on your behalf. The `.env` file is already excluded from git via `.gitignore`, so it can't end up on GitHub by accident if you ever push your own changes to a repo. If a key is ever accidentally exposed, revoke it immediately on your provider's console and issue a new one.

**Everything else sensitive** -- the vector database (`chroma_db/`), extracted photos (`photos/`), your raw export files, and the chat history log -- also lives only on your computer and is excluded from git the same way. No one else, including whoever built this project, ever has access to any of it. This isn't a hosted service; there's no server anywhere except the one running on your own machine.

**On network exposure specifically:** the setup wizard (`setup_wizard.py`) only ever listens on `127.0.0.1` -- your own computer -- and is never reachable from your phone or any other device, even over Tailscale, since it's the one page that handles your raw API key. The main chat app (`webapp/server.py`) does listen more broadly (`0.0.0.0:5000`) specifically so it's reachable from your phone over Tailscale -- but again, it has no login of its own, so treat that port the way you'd treat a door with no lock: fine on a private network you control (Tailscale), risky on shared/public Wi-Fi without Tailscale, and something you should never expose to the open internet by port-forwarding it on your router.

Full details live in [SECURITY.md](SECURITY.md).

## If something goes wrong

- **The setup wizard's browser tab shows an error, or never opens:** go to `http://localhost:5050` manually. If that also fails, check the terminal window the launcher opened -- the actual error is usually printed there.
- **`python` isn't recognized / command not found:** Python isn't installed, or wasn't added to your system's PATH during install. Re-run the installer from [python.org](https://www.python.org/downloads/) and, on Windows, make sure "Add python.exe to PATH" is checked.
- **Ingestion fails with an "Unrecognized export format" error:** your export's JSON structure doesn't match any of the supported shapes. The error message itself will show you what structure was actually found -- see [Known limitations](#known-limitations-please-read-this-section) above, especially if you're using Diarium or another app.
- **Nothing else on this list matches your problem:** open a GitHub issue on this project's repository, including the exact error message and what you were trying to do. Please leave out your actual journal content, your API key, and any real names/locations from anything you paste in.

## Project structure

```
journal-rag/
├── start_setup.bat          # Windows: double-click this first
├── start_setup.command      # Mac: double-click this first
├── setup_wizard.py          # The setup page these launchers open
├── ask.py                   # Question routing + retrieval logic
├── ingest.py                 # Reads journal exports into the local database, extracts tags
├── tools/
│   ├── tag_backfill.py       # Catches up entries missing tags, in resumable batches
│   ├── backfill_photo_metadata.py
│   ├── diagnose_photos.py
│   └── inspect_photo_meta.py
├── config.py                 # All settings in one place
├── watcher.py                 # Optional: auto-ingest new exports + auto-start the server
├── webapp/
│   ├── server.py             # The chat web app's server
│   └── templates/
│       └── index.html        # The chat UI itself
├── tests/
│   └── test_normalize_entries.py   # Automated tests for the export-format parsing
├── .env                       # Your API key + paths (created by setup_wizard.py, gitignored)
├── .gitignore                 # Keeps your journal data and secrets out of git
├── LICENSE                    # MIT
└── SECURITY.md                # Full details on data handling and network exposure
```

## License

[MIT](LICENSE) -- do what you like with it.
