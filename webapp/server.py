"""
Small local web server for the "ask your journal" chat UI.
Wraps the same retrieval + Claude logic as ask.py, just behind a
proper chat interface instead of the command line.

Run:
    export ANTHROPIC_API_KEY="your-key-here"
    python server.py

Then, on your phone (via Tailscale), visit:
    http://<your-tailscale-hostname>:5000
"""

import sys
import os
import json
import socket
import subprocess
from datetime import datetime

from flask import Flask, request, jsonify, render_template, send_from_directory
import chromadb

# Reuse config + helpers from the parent pipeline
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
import llm
from ask import (
    retrieve_context, build_prompt, rough_token_estimate,
    is_visual_search_question, retrieve_visual_context, build_visual_prompt,
    retrieve_combined_context, build_combined_prompt,
    is_statement, build_statement_prompt,
    is_synthesis_question, is_trend_question,
)
from ingest import PHOTOS_DIR, strip_photo_tokens

app = Flask(__name__)
# Auto-reload templates from disk on every request instead of caching the
# compiled Jinja template in memory for the life of the process. Without
# this, editing index.html has no effect until the server is restarted --
# debug=False (see bottom of this file) leaves Jinja's default auto_reload
# off, which is what caused that exact confusion during development.
app.jinja_env.auto_reload = True

_client = None
_collection = None

# In-memory conversation history per session -- keeps the last N turns so
# "tell me more about that" works. Keyed by session_id from the frontend.
# Cleared on server restart (intentional -- no persistence needed here).
_conversation_histories = {}
CONVERSATION_MAX_TURNS = 6  # keep last 6 Q&A pairs in context

# Simple local history log -- one line per Q&A, newest last.
HISTORY_LOG = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "chat_history.jsonl",
)


# Month name -> number map for matching human-readable dates in answers
# Month number -> every spelling Claude might use when it writes a date in
# prose, so date-matching works whether the model wrote "Apr 28", "April
# 28", or "Sept 28". Having ONLY the 3-letter abbreviations here was a real
# bug: when Claude wrote the full name ("April 28, 2026") every source got
# filtered out and the answer showed no "View entry" buttons at all.
_MONTH_NAMES = {
    "01": ("jan", "january"), "02": ("feb", "february"),
    "03": ("mar", "march"),   "04": ("apr", "april"),
    "05": ("may",),           "06": ("jun", "june"),
    "07": ("jul", "july"),    "08": ("aug", "august"),
    "09": ("sep", "sept", "september"), "10": ("oct", "october"),
    "11": ("nov", "november"), "12": ("dec", "december"),
}

def _answer_mentions_date(answer_lower, iso_date):
    """
    Returns True if the answer text mentions this date in any common format:
      - ISO:        2026-01-19
      - Long:       January 19, 2026  /  Jan 19, 2026
      - Short:      Jan 19  /  January 19
    """
    if not iso_date or len(iso_date) < 10:
        return False

    day_iso = iso_date[:10]  # "YYYY-MM-DD"
    if day_iso in answer_lower:
        return True

    # Parse the ISO date into parts
    try:
        year, month, day = day_iso.split("-")
    except ValueError:
        return False

    day_int = int(day)

    # Check every spelling for this month -- abbreviated and full.
    for name in _MONTH_NAMES.get(month, ()):
        # "apr 28, 2026" / "april 28, 2026" / "apr 28 2026" / "apr. 28"
        patterns = [
            f"{name} {day_int},",
            f"{name} {day_int} {year}",
            f"{name}. {day_int}",
        ]
        for p in patterns:
            if p in answer_lower:
                return True

    return False


def _filter_sources_to_answer(sources, answer):
    """
    Keeps only sources whose date appears in Claude's answer in any common
    date format (ISO, "Jan 19, 2026", "January 19", etc). Without this,
    sources that Claude never actually cited would show up as orphaned photos.

    Skipped for synthesis/trend questions (callers handle that).
    """
    answer_lower = answer.lower()
    return [
        s for s in sources
        if _answer_mentions_date(answer_lower, s.get("date") or "")
    ]


def append_history(question, answer, sources, timestamp):
    entry = {
        "question": question,
        "answer": answer,
        "sources": sources,
        "timestamp": timestamp,
    }
    with open(HISTORY_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def load_history(limit=50):
    if not os.path.exists(HISTORY_LOG):
        return []
    with open(HISTORY_LOG, "r", encoding="utf-8") as f:
        lines = f.readlines()
    entries = [json.loads(line) for line in lines[-limit:]]
    return entries


def get_collection():
    global _client, _collection
    if _collection is None:
        _client = chromadb.PersistentClient(path=config.VECTOR_DB_DIR)
        _collection = _client.get_or_create_collection("journal")
    return _collection


def _require_llm():
    """Raise a clear error if the active provider isn't configured yet."""
    if not llm.is_configured():
        raise RuntimeError(
            f"No API key configured for {llm.provider_name()}. "
            f"Run setup_wizard.py first."
        )


@app.route("/")
def index():
    return render_template("index.html")


def _wizard_already_running():
    try:
        with socket.create_connection(("127.0.0.1", 5050), timeout=0.5):
            return True
    except OSError:
        return False


@app.route("/open-settings")
def open_settings():
    """
    Opens the setup wizard (folders, API key, provider) without needing a
    terminal. If it isn't already running on :5050, starts it in the
    background first; either way, the page below waits until it responds
    and then sends the browser there.
    """
    if not _wizard_already_running():
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        env = os.environ.copy()
        env["JOURNAL_WIZARD_EMBEDDED"] = "1"
        subprocess.Popen(
            [sys.executable, "setup_wizard.py"],
            cwd=project_root,
            env=env,
        )

    return """<!doctype html><html><head><meta charset="utf-8">
<title>Opening settings&hellip;</title>
<style>
  body{font-family:system-ui,-apple-system,sans-serif;display:flex;
       align-items:center;justify-content:center;height:100vh;margin:0;
       background:#faf9f6;color:#3a352e;text-align:center;padding:0 24px}
</style></head><body>
<p id="msg">Opening settings&hellip;</p>
<script>
  var tries = 0;
  function check() {
    fetch("http://127.0.0.1:5050", {mode: "no-cors"}).then(function () {
      window.location.href = "http://127.0.0.1:5050";
    }).catch(function () {
      tries++;
      if (tries > 40) {
        document.getElementById("msg").textContent =
          "Still starting -- if this doesn't open in a few seconds, " +
          "open http://localhost:5050 yourself.";
      }
      setTimeout(check, 250);
    });
  }
  check();
</script>
</body></html>"""


_FOLLOWUP_CUES = (
    "show me again", "again", "more", "another", "what about", "how about",
    "that one", "those", "them", "the same", "keep going", "next", "continue",
    "as well", "also show", "and the", "what else",
)


def _looks_like_followup(question):
    """
    Heuristic for whether a question leans on the previous turn instead of
    standing on its own. Short questions ("show me again", "the beach?") and
    ones carrying an explicit follow-up cue qualify. Kept deliberately loose:
    a false positive only costs one cheap Haiku rewrite that returns the
    query roughly unchanged, whereas a false negative means a follow-up
    silently retrieves on meaningless words -- so we bias toward catching them.
    """
    q = question.strip().lower()
    if len(q.split()) <= 4:
        return True
    return any(cue in q for cue in _FOLLOWUP_CUES)


def _reformulate_for_retrieval(question, history):
    """
    Turn a follow-up ("show me again", "what about the beach") into a
    standalone search query using recent conversation, so the RETRIEVAL step
    has real topic words to match. Without this, a short follow-up searches
    on its own literal words -- which carry no topic -- and comes back empty
    even when the previous turn made the intent obvious.

    Only fires on questions that actually look like follow-ups, so ordinary
    standalone questions incur no extra model call. The result is used for
    retrieval ONLY -- the user's original words are still what gets stored,
    displayed, and shown to Claude when composing the answer.

    "Both" strategy, chosen for robustness at minimal cost:
      1. Always blend in the previous user question's text -- a zero-cost
         safety net guaranteeing the prior topic's words reach the search.
      2. Additionally ask the cheap tag model (Haiku) to rewrite the message
         into a clean standalone query, which resolves pronouns and implicit
         references the raw blend can't. If it's unavailable or errors, the
         blend alone still helps.
    """
    if not history or not _looks_like_followup(question):
        return question

    prev_user = ""
    for turn in reversed(history):
        if turn.get("role") == "user":
            prev_user = turn.get("content", "") or ""
            break
    blended = (prev_user + " " + question).strip() if prev_user else question

    try:
        if llm.is_configured():
            recent = history[-4:]
            convo = "\n".join(f"{t.get('role')}: {t.get('content')}" for t in recent)
            rewrite_prompt = (
                "Rewrite the user's latest message into a single standalone "
                "search query for looking up their own journal entries, using "
                "the conversation for context. Resolve references like 'again', "
                "'more', 'that', 'those', 'them'. Reply with ONLY the query "
                "text -- no quotes, no explanation.\n\n"
                f"Conversation:\n{convo}\n\nLatest message: {question}\n\n"
                "Standalone query:"
            )
            rewritten, _ = llm.chat(
                rewrite_prompt, model=llm.get_tag_model(), max_tokens=60
            )
            rewritten = (rewritten or "").strip().strip('"').strip()
            if rewritten:
                # Keep both the model's cleaned query and the raw blended
                # words -- belt and suspenders for retrieval recall.
                return (rewritten + " " + blended).strip()
    except Exception:
        pass
    return blended


@app.route("/api/ask", methods=["POST"])
def ask():
    data = request.get_json(force=True)
    question = (data.get("question") or "").strip()
    session_id = (data.get("session_id") or "default").strip()
    # "Max Recall" toggle from the chat UI -- forces tag-based completeness
    # retrieval (see ask.py's retrieve_combined_context) even on questions
    # that don't auto-detect as a topic question by keyword.
    max_recall = bool(data.get("max_recall"))
    if not question:
        return jsonify({"error": "Empty question."}), 400

    # Retrieve or create conversation history for this session
    history = _conversation_histories.get(session_id, [])

    collection = get_collection()
    if collection.count() == 0:
        return jsonify({
            "error": "Your journal hasn't been ingested yet. Run ingest.py first."
        }), 400

    # Resolve follow-ups ("show me again", "what about the beach") into a
    # standalone query for RETRIEVAL only -- the original `question` is still
    # what's stored, displayed, and handed to Claude for the actual answer.
    retrieval_question = _reformulate_for_retrieval(question, history)

    # Statement detection -- respond warmly instead of searching
    if is_statement(question):
        try:
            _require_llm()
            statement_prompt = build_statement_prompt(question)
            messages = history + [{"role": "user", "content": statement_prompt}]
            answer, _usage = llm.chat(messages, max_tokens=200)
        except Exception as e:
            return jsonify({"error": str(e)}), 500
        # Update conversation history with the original question and answer
        # NOT the internal prompt that contains old context blocks
        history = (history + [
            {"role": "user", "content": question},
            {"role": "assistant", "content": answer},
        ])[-CONVERSATION_MAX_TURNS * 2:]
        _conversation_histories[session_id] = history
        return jsonify({
            "answer": answer,
            "sources": [],
            "usage": None,
            "warned_large": False,
            "timestamp": datetime.now().strftime("%b %d, %Y"),
            "is_visual_search": False,
        })

    # Phase 4: explicit photo search questions route to visual-only
    visual_matches = None
    if is_visual_search_question(retrieval_question):
        visual_matches = retrieve_visual_context(retrieval_question, collection)

    if visual_matches:
        visual_prompt = build_visual_prompt(question, visual_matches)
        est_tokens = rough_token_estimate(visual_prompt)
        try:
            _require_llm()
            messages = history + [{"role": "user", "content": visual_prompt}]
            answer, usage_info = llm.chat(messages, max_tokens=1000)
        except Exception as e:
            return jsonify({"error": str(e)}), 500
        candidate_sources = [
            {"date": date or "unknown", "photo": photo_id, "is_visual": True,
             "entry_id": entry_id or None}
            for photo_id, date, text, score, entry_id in visual_matches
        ]
        # Normally, strict-filter down to only the photos whose date Claude
        # actually cited -- that keeps confident answers tight and avoids
        # showing a matched photo Claude decided wasn't relevant. But when
        # Claude is UNSURE (e.g. "I don't see a sea lion in these"), it cites
        # no dates at all, so the strict filter would drop every candidate
        # and leave nothing to look at. In that specific case, fall back to
        # the closest visual matches unfiltered, so the user can flip through
        # them in the View-entry carousel and judge for themselves -- the
        # "idk what you mean, but here are the closest guesses" behavior.
        sources = _filter_sources_to_answer(candidate_sources, answer)
        if not sources:
            sources = candidate_sources
        timestamp = datetime.now().strftime("%b %d, %Y")
        append_history(question, answer, sources, timestamp)
        # Store original question in history, not the prompt with embedded context
        history = (history + [
            {"role": "user", "content": question},
            {"role": "assistant", "content": answer},
        ])[-CONVERSATION_MAX_TURNS * 2:]
        _conversation_histories[session_id] = history
        return jsonify({
            "answer": answer,
            "sources": sources,
            "usage": usage_info,
            "warned_large": est_tokens > config.MAX_TOKENS_PER_QUERY_WARNING,
            "timestamp": timestamp,
            "is_visual_search": True,
        })

    # Phase 5: combined text + visual for all other questions
    combined = retrieve_combined_context(retrieval_question, collection, force_max=max_recall)
    combined_prompt = build_combined_prompt(question, combined)
    est_tokens = rough_token_estimate(combined_prompt)

    try:
        _require_llm()
        messages = history + [{"role": "user", "content": combined_prompt}]
        answer, usage_info = llm.chat(messages, max_tokens=1000)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    if combined.get("low_confidence"):
        # build_combined_prompt() ignores chunks/photos entirely and tells
        # Claude to say "I don't see relevant entries" in this case -- so
        # the sources shown to the user need to match that.
        sources = []
    else:
        context_chunks = combined["chunks"]
        photo_results = combined["photos"]

        # Merge text sources and ambient photo sources into one list
        text_sources = [
            {"date": meta.get("date", "unknown"), "photo": meta.get("photo") or None,
             "entry_id": meta.get("entry_id") or None}
            for _, meta in context_chunks
        ]
        photo_sources = [
            {"date": date or "unknown", "photo": photo_id, "is_visual": True,
             "entry_id": entry_id or None}
            for photo_id, date, text, score, entry_id in photo_results
        ]
        sources = text_sources + photo_sources

        # Strict date-matching only for regular Q&A. Synthesis/trend
        # questions deliberately pull in a wide swath of context that
        # Claude summarizes rather than cites entry-by-entry, so this
        # filter is skipped for those -- keeps the current "show
        # everything retrieved" behavior there on purpose.
        # Tag-matched "Max Recall" answers behave like synthesis/trend
        # answers here too -- Claude is working from the FULL tagged set
        # and summarizing/organizing it, not citing entry-by-entry, so the
        # strict per-line date filter would incorrectly drop most sources.
        if not (is_synthesis_question(question) or is_trend_question(question) or combined.get("matched_tag")):
            sources = _filter_sources_to_answer(sources, answer)

    timestamp = datetime.now().strftime("%b %d, %Y")
    append_history(question, answer, sources, timestamp)

    # Update conversation memory for follow-up questions
    # Store the original question + answer, NOT the full prompt with embedded context
    # This prevents old context blocks from being carried into subsequent queries
    history = (history + [
        {"role": "user", "content": question},
        {"role": "assistant", "content": answer},
    ])[-CONVERSATION_MAX_TURNS * 2:]
    _conversation_histories[session_id] = history

    return jsonify({
        "answer": answer,
        "sources": sources,
        "usage": usage_info,
        "warned_large": est_tokens > config.MAX_TOKENS_PER_QUERY_WARNING,
        "timestamp": timestamp,
        "is_visual_search": False,
        "matched_tag": combined.get("matched_tag"),
    })


@app.route("/api/entry/<entry_id>")
def get_entry(entry_id):
    """
    Returns the full reassembled text (all chunks joined back together, in
    order) plus date and photo for one journal entry, keyed by entry_id.
    This is what powers the "Read full entry" popup in the chat UI --
    without it, that button in index.html has nothing to call.
    """
    collection = get_collection()
    result = collection.get(
        where={"entry_id": entry_id},
        include=["documents", "metadatas"],
    )
    docs = result.get("documents", [])
    metas = result.get("metadatas", [])
    ids = result.get("ids", [])

    if not docs:
        return jsonify({"error": "Entry not found."}), 404

    # Chunk ids look like "{entry_id}_{chunk_index}" (see ingest.py), so
    # sort on that trailing index to reassemble long entries in the
    # correct order -- collection.get() doesn't guarantee chunk order.
    def chunk_index(chunk_id):
        tail = chunk_id.rsplit("_", 1)[-1]
        return int(tail) if tail.isdigit() else 0

    order = sorted(range(len(ids)), key=lambda i: chunk_index(ids[i]))
    # strip_photo_tokens here (not just at ingest) so entries embedded
    # BEFORE this fix also display cleanly -- no costly --force re-ingest
    # needed just to hide the raw ![](dayone-moment://...) tokens.
    full_text = strip_photo_tokens("".join(docs[i] for i in order))

    date = metas[0].get("date", "") if metas else ""
    photo = next((m.get("photo") for m in metas if m.get("photo")), None)

    return jsonify({"date": date, "photo": photo, "text": full_text})


@app.route("/photos/<path:filename>")
def serve_photo(filename):
    # send_from_directory guards against path traversal (e.g. ../../etc)
    # automatically -- safe to expose directly like this.
    return send_from_directory(PHOTOS_DIR, filename)


@app.route("/api/history")
def history():
    return jsonify({"history": load_history()})


@app.route("/api/status")
def status():
    collection = get_collection()
    stats = {"entry_chunks": collection.count()}
    # How many journal entries have been ingested (from the ingest log) and when
    # that last happened -- so the header reflects the real state of your journal,
    # not just the raw chunk count.
    try:
        proc_log = config.PROCESSED_LOG
        if not os.path.isabs(proc_log):
            # Resolve relative to the project root, not the current working
            # directory, so the stats work no matter how the server was started.
            _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            proc_log = os.path.join(_root, proc_log)
        if os.path.exists(proc_log):
            with open(proc_log, "r", encoding="utf-8") as f:
                stats["entries"] = len(json.load(f))
            stats["last_updated"] = os.path.getmtime(proc_log)
    except Exception:
        pass
    # How many photos are available locally for visual search.
    try:
        if os.path.isdir(PHOTOS_DIR):
            stats["photos"] = sum(1 for e in os.scandir(PHOTOS_DIR) if e.is_file())
    except Exception:
        pass
    return jsonify(stats)


@app.route("/api/mood")
def mood():
    """
    Returns monthly average sentiment scores so the frontend can draw a
    mood-over-time chart. Sentiment is stored at ingest time as a float
    from -1.0 (very negative) to +1.0 (very positive) in chunk metadata.
    Falls back gracefully if no sentiment data exists yet.
    """
    collection = get_collection()
    if collection.count() == 0:
        return jsonify({"months": []})

    all_data = collection.get(include=["metadatas"])
    metas = all_data.get("metadatas", [])

    monthly = {}
    for meta in metas:
        sentiment = meta.get("sentiment")
        date = (meta.get("date") or "")[:7]  # "YYYY-MM"
        if sentiment is not None and date:
            if date not in monthly:
                monthly[date] = []
            monthly[date].append(float(sentiment))

    if not monthly:
        return jsonify({"months": [], "note": "No sentiment data yet -- re-run ingest.py to add it."})

    result = sorted([
        {"month": month, "avg_sentiment": round(sum(scores) / len(scores), 3), "entry_count": len(scores)}
        for month, scores in monthly.items()
    ], key=lambda x: x["month"])

    return jsonify({"months": result})


@app.route("/api/clear_history", methods=["POST"])
def clear_session_history():
    """Clears conversation memory for a session -- lets the user start fresh."""
    data = request.get_json(force=True)
    session_id = (data.get("session_id") or "default").strip()
    _conversation_histories.pop(session_id, None)
    return jsonify({"cleared": True})


if __name__ == "__main__":
    # By default the server binds 0.0.0.0 so it's reachable from your phone
    # over your LAN or Tailscale -- convenient, but it means any device on
    # your network can reach it. If you only ever use it on THIS computer,
    # set SERVER_HOST=127.0.0.1 in your .env to lock it to localhost only
    # (more private/secure). Never expose this port to the public internet.
    _host = os.environ.get("SERVER_HOST", "0.0.0.0")
    _port = int(os.environ.get("SERVER_PORT", "5000"))
    app.run(host=_host, port=_port, debug=False)