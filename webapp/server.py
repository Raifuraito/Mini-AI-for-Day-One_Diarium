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
from ingest import PHOTOS_DIR

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
_MONTH_MAP = {
    "jan": "01", "feb": "02", "mar": "03", "apr": "04",
    "may": "05", "jun": "06", "jul": "07", "aug": "08",
    "sep": "09", "oct": "10", "nov": "11", "dec": "12",
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

    # Check every month-name variant
    for abbr, num in _MONTH_MAP.items():
        if num != month:
            continue
        # "jan 19, 2026" / "jan 19 2026" / "jan 19"
        patterns = [
            f"{abbr} {day_int},",
            f"{abbr} {day_int} {year}",
            f"{abbr}. {day_int}",
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
    if is_visual_search_question(question):
        visual_matches = retrieve_visual_context(question, collection)

    if visual_matches:
        visual_prompt = build_visual_prompt(question, visual_matches)
        est_tokens = rough_token_estimate(visual_prompt)
        try:
            _require_llm()
            messages = history + [{"role": "user", "content": visual_prompt}]
            answer, usage_info = llm.chat(messages, max_tokens=1000)
        except Exception as e:
            return jsonify({"error": str(e)}), 500
        sources = [
            {"date": date or "unknown", "photo": photo_id, "is_visual": True,
             "entry_id": entry_id or None}
            for photo_id, date, text, score, entry_id in visual_matches
        ]
        # Photo search always gets the strict filter -- if Claude didn't
        # end up mentioning a matched photo's date at all, don't show it.
        sources = _filter_sources_to_answer(sources, answer)
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
    combined = retrieve_combined_context(question, collection, force_max=max_recall)
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
    full_text = "".join(docs[i] for i in order)

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
    return jsonify({"entry_chunks": collection.count()})


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
    # host=0.0.0.0 so it's reachable over Tailscale, not just localhost.
    app.run(host="0.0.0.0", port=5000, debug=False)