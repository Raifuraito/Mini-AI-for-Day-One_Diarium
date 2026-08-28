"""
Shared configuration for the journal RAG pipeline.
Edit these values for your setup.
"""

import os

# --- Optional .env file support ---
# Lets setup_wizard.py (or hand-editing a .env file) configure this app
# without touching a terminal's environment variables. A real environment
# variable set via `export`/`setx` always wins over a value from .env, so
# this never overrides anyone already using that approach -- it only fills
# in values that aren't set yet.
def _load_dotenv(path=".env"):
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


_load_dotenv()

# --- Paths ---
# Folder where your journal exports land (synced folder for Diarium,
# or manual export drop folder for Day One).
EXPORT_WATCH_DIR = os.environ.get("JOURNAL_EXPORT_DIR", "./exports")

# Where the vector DB lives (local, persistent, free).
VECTOR_DB_DIR = os.environ.get("JOURNAL_DB_DIR", "./chroma_db")

# Tracks which entries have already been embedded, so re-runs only
# process new/changed entries instead of re-embedding everything.
PROCESSED_LOG = os.environ.get("JOURNAL_PROCESSED_LOG", "./processed_entries.json")

# Tracks which entries tag_backfill.py has already attempted to tag, so an
# entry Claude found nothing notable in doesn't get re-sent (and re-paid
# for) every time you run it again.
TAG_BACKFILL_LOG = os.environ.get("JOURNAL_TAG_LOG", "./tag_backfill_log.json")

# --- Anthropic API ---
# Set this in your environment (or via setup_wizard.py); never hardcode a
# key in this file.
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = "claude-sonnet-4-6"  # good balance of cost/quality for Q&A

# --- Embeddings ---
# Chroma's default local embedding function runs free, on-device (no API cost).
# Set to True to use a hosted embedding API instead (costs a small amount,
# slightly better quality). Leave False unless you have a reason to change it.
USE_HOSTED_EMBEDDINGS = False

# --- Cost guardrails ---
# Caps how many journal chunks get stuffed into context per question.
# More chunks = better recall but more tokens = more cost.
MAX_CONTEXT_CHUNKS = 8

# Higher cap used only for questions that clearly ask about a trend,
# pattern, week, or month ("how have I been feeling lately", "any
# patterns in my week") -- these genuinely need more spread-out context
# to answer well. Single-fact questions keep the cheaper MAX_CONTEXT_CHUNKS
# limit above; only detected trend/range questions use this higher one.
TREND_MAX_CONTEXT_CHUNKS = 25

# Widest cap, used only for genuine synthesis/organizing questions --
# "summarize my meeting notes and give me action items", "what should I
# follow up on this week". These need the broadest context since missing
# an entry here means a missed follow-up, not just a thinner answer.
# Costs more per question (roughly 2-4 cents at this size) but only fires
# for questions that actually ask to combine/organize/synthesize -- see
# SYNTHESIS_KEYWORDS in ask.py.
SYNTHESIS_MAX_CONTEXT_CHUNKS = 50

# Rough token budget per query (context + question + answer). This is a
# soft safety check, not a hard API-enforced limit -- it just warns you
# before sending an unusually large request. Raised to accommodate the
# synthesis tier without warning on every single synthesis question.
MAX_TOKENS_PER_QUERY_WARNING = 12000

# Chunking: split entries longer than this into multiple pieces so
# embeddings stay focused and retrieval is more precise.
CHUNK_SIZE_CHARS = 1500
CHUNK_OVERLAP_CHARS = 200

# --- Phase 4: visual search ---
# How many photos to pull back from the CLIP similarity search per visual
# question, before their entries get sent to Claude for a written
# description. Kept separate from MAX_CONTEXT_CHUNKS since it caps photos,
# not text chunks.
MAX_VISUAL_RESULTS = 5

# --- Phase 5: ambient visual search ---
# How many entries with photos to show on every non-photo-specific question.
# Photos follow entries, so this controls entry count, not photo count.
# Examples: 3 = show up to 3 entries with photos, 5 = up to 5, 10 = up to 10, etc.
PHASE5_MAX_ENTRY_RESULTS = 3

# Minimum CLIP similarity score (0-1) for a photo to be included in a
# normal (non-photo-specific) answer. Raised to 0.30 to cut false
# positives (e.g. a Crisis Text Line sign matching a California trip).
# Lower it toward 0.20 if you find relevant photos are being missed.
PHASE5_VISUAL_THRESHOLD = 0.30

# --- Diversity sampling for repeated questions ---
# When a diversity question ("give me more", "tell me more") retrieves this many
# or more entries, apply random sampling to show variety on repeated asks.
# Examples: 3 = sample if 3+ entries, 5 = sample if 5+ entries, etc.
# Set lower to sample more aggressively, higher to be more conservative.
DIVERSITY_ENTRY_THRESHOLD = 3

# For diversity questions, cap the maximum number of entries shown.
# Examples: 5 = show max 5 entries, 10 = show max 10, 20 = show max 20, None = no cap
# This controls how many entries (and thus photos) appear in the response.
DIVERSITY_MAX_ENTRIES = None  # None = show all entries
# --- Entry tagging (people/places/themes) ---
# Extracted once per entry at ingest time (not once per question) and used
# by "Max Recall" retrieval below to guarantee completeness for topic
# questions -- pulling EVERY entry tagged with a topic, not just the top-K
# most semantically similar ones.
#
# Model used for tag extraction. Defaults to the same model as everything
# else; point this at a cheaper/faster one if you want to cut cost.
TAG_EXTRACTION_MODEL = "claude-haiku-4-5"  # Haiku instead of Sonnet -- tagging just
# extracts short people/places/theme lists, doesn't need Sonnet's quality, and
# Haiku is roughly 1/3 the price. Q&A itself (CLAUDE_MODEL, above) stays on Sonnet.

# How many entries to send to Claude per tagging call during ingest.
# Batched rather than one call per entry -- with 1000+ entries, one call
# each would mean 1000+ separate API round trips on a --force backfill.
TAG_BATCH_SIZE = 15

# How much of each entry's text to include per tagging call. Tags don't
# need the full entry, just enough to identify people/places/themes --
# keeping this short keeps batch prompts (and cost) down.
TAG_SNIPPET_CHARS = 800

# --- "Max Recall" mode ---
# Ceiling on chunks pulled for a tag-matched "give me everything about X"
# retrieval -- auto-detected (see TOPIC_KEYWORDS in ask.py) or forced by
# the Max Recall toggle in the chat UI. Deliberately much higher than
# SYNTHESIS_MAX_CONTEXT_CHUNKS above -- completeness is the whole point
# here, this is a sanity ceiling, not a target.
MAX_POWER_CONTEXT_CHUNKS = 200
