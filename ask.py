"""
Ask your journal a question. Retrieves the most relevant chunks from the
vector DB, stuffs only those into context (not your whole journal), and
sends a single request to the Claude API.

Usage:
    python ask.py "What was going on with me last spring?"
"""

import sys
import re
import random
import calendar
import difflib
from datetime import datetime

import chromadb
from anthropic import Anthropic

import config

MONTH_NAMES = {name.lower(): i for i, name in enumerate(calendar.month_name) if name}
MONTH_NAMES.update({name.lower(): i for i, name in enumerate(calendar.month_abbr) if name})

TREND_KEYWORDS = (
    "trend", "pattern", "lately", "over time", "in general", "overall",
    "how have i been", "how has my", "how am i", "week", "month",
    "recently", "these days", "changed", "progress", "growth",
)

SYNTHESIS_KEYWORDS = (
    "action item", "action items", "to-do", "todo", "to do list",
    "follow up", "follow-up", "next steps", "what should i",
    "summarize my", "summarize the", "across days", "across my",
    "combine", "consolidate", "organize my", "everything i",
)

# Questions asking for EVERY entry about a topic, not just the top few --
# these trigger tag-based "Max Recall" retrieval (see retrieve_tag_context)
# when the topic also matches a real tag extracted at ingest time. This is
# separate from SYNTHESIS_KEYWORDS above: synthesis is about *how* to
# present results (combine + action items), this is about *completeness*
# of what gets retrieved in the first place.
TOPIC_KEYWORDS = (
    "everything about", "everything related to", "everything i wrote about",
    "everything i've written about", "everything ive written about",
    "all about my", "all my entries about", "all entries about",
    "all the times i", "full history of", "complete history of",
    "the whole story of", "all my thoughts on", "give me everything",
    "my whole history with", "journal about my", "all mentions of",
)

VISUAL_KEYWORDS = (
    "photo", "picture", "image", "pic ", "pics ", "snapshot",
    "what did i look like", "what did it look like", "how did i look",
)

# Questions where photos would be genuine noise -- philosophical, abstract,
# or introspective questions that CLIP will match poorly anyway.
PHOTO_SUPPRESS_KEYWORDS = (
    "purpose", "meaning", "why do i", "what is my", "who am i",
    "what should i do with", "advice", "what do you think",
    "how do i feel about", "do i believe", "philosophy",
)

# Questions that explicitly ask for forgotten/neglected entries -- we sort
# by date ascending (oldest first) so buried memories surface, not recent ones.
STALENESS_KEYWORDS = (
    "haven't reflected", "not reflected", "forgotten", "haven't thought about",
    "haven't revisited", "buried", "old entries", "forgotten thread",
    "what have i not", "neglected", "haven't written about",
    "what did i used to", "long time ago", "ages ago",
)

# Statements rather than questions -- we detect these so we can respond
# warmly instead of doing a confused RAG search over nothing.
STATEMENT_PATTERNS = (
    r"^i am ",
    r"^i'm ",
    r"^i love ",
    r"^i hate ",
    r"^i like ",
    r"^i enjoy ",
    r"^i think ",
    r"^i feel ",
    r"^i believe ",
    r"^i want ",
    r"^i need ",
    r"^i wish ",
    r"^just ",
    r"^fyi",
    r"^note:",
    r"^reminder:",
)


def is_trend_question(question):
    q = question.lower()
    return any(kw in q for kw in TREND_KEYWORDS)


def is_synthesis_question(question):
    q = question.lower()
    return any(kw in q for kw in SYNTHESIS_KEYWORDS)


def is_topic_question(question):
    q = question.lower()
    return any(kw in q for kw in TOPIC_KEYWORDS)


def is_visual_search_question(question):
    q = question.lower()
    return any(kw in q for kw in VISUAL_KEYWORDS)


def should_suppress_photos(question):
    """
    Returns True for abstract/philosophical questions where pulling in
    photos would be noise. CLIP similarity on these is unreliable anyway
    since there's no visual concept to match against.
    """
    q = question.lower()
    return any(kw in q for kw in PHOTO_SUPPRESS_KEYWORDS)


def is_statement(question):
    """
    Detects when the person typed a statement rather than a question --
    "I'm a foodie", "I love hiking" etc. These should get a warm
    acknowledgment that relates it back to their journal, not a search.
    """
    q = question.lower().strip()
    # Strip punctuation for matching
    q_clean = re.sub(r'[.!?,;:]+$', '', q).strip()
    return any(re.match(p, q_clean) for p in STATEMENT_PATTERNS)


def is_staleness_question(question):
    """
    Returns True for questions asking about entries that haven't been
    revisited or reflected on in a long time -- these get sorted oldest-
    first so buried memories surface rather than recent ones.
    """
    q = question.lower()
    return any(kw in q for kw in STALENESS_KEYWORDS)


def rough_token_estimate(text):
    return len(text) // 4


def detect_date_range(question):
    q = question.lower()

    month_num = None
    for name, num in MONTH_NAMES.items():
        if re.search(rf"\b{name}\b", q):
            month_num = num
            break

    if not month_num:
        # Fuzzy fallback for typos like "janurary" -> "january" or
        # "feburary" -> "february". Only matched against full month names
        # (not 3-letter abbreviations -- those are too short for fuzzy
        # matching to be reliable and would cause false positives on
        # unrelated short words). cutoff=0.75 is fairly strict so it
        # catches typos without matching unrelated words by accident.
        full_month_names = [name.lower() for name in calendar.month_name if name]
        for word in re.findall(r"[a-z]+", q):
            if len(word) < 4:
                continue
            match = difflib.get_close_matches(word, full_month_names, n=1, cutoff=0.75)
            if match:
                month_num = full_month_names.index(match[0]) + 1
                break

    if not month_num:
        return None

    year_match = re.search(r"\b(20\d{2})\b", q)
    year = int(year_match.group(1)) if year_match else datetime.now().year

    last_day = calendar.monthrange(year, month_num)[1]
    start_day, end_day = 1, last_day

    week_match = re.search(r"\b(first|1st|second|2nd|third|3rd|fourth|4th|last)\s+week\b", q)
    if week_match:
        w = week_match.group(1)
        if w in ("first", "1st"):
            start_day, end_day = 1, min(7, last_day)
        elif w in ("second", "2nd"):
            start_day, end_day = 8, min(14, last_day)
        elif w in ("third", "3rd"):
            start_day, end_day = 15, min(21, last_day)
        elif w in ("fourth", "4th"):
            start_day, end_day = 22, min(28, last_day)
        elif w == "last":
            start_day, end_day = max(1, last_day - 6), last_day

    start_iso = f"{year:04d}-{month_num:02d}-{start_day:02d}"
    end_iso = f"{year:04d}-{month_num:02d}-{end_day:02d}T23:59:59"
    return (start_iso, end_iso)


def effective_chunk_cap(question, default_n, force_max=False):
    if force_max:
        return config.MAX_POWER_CONTEXT_CHUNKS
    if is_synthesis_question(question):
        return config.SYNTHESIS_MAX_CONTEXT_CHUNKS
    if is_trend_question(question):
        return config.TREND_MAX_CONTEXT_CHUNKS
    return default_n


def retrieve_context(question, collection, n_results=None, diversify=False, force_max=False):
    """
    Retrieves relevant text chunks. If diversify=True, applies a light
    shuffle to the top results so repeated similar questions don't always
    surface the exact same entry -- preserving relevance while adding variety.
    force_max=True raises the chunk cap to config.MAX_POWER_CONTEXT_CHUNKS
    (see effective_chunk_cap) -- used as a fallback when Max Recall is on
    but the question didn't match any known tag, so the toggle still does
    something instead of silently being ignored.
    """
    n_results = n_results or config.MAX_CONTEXT_CHUNKS

    date_range = detect_date_range(question)
    if date_range:
        start_iso, end_iso = date_range
        all_results = collection.get(include=["documents", "metadatas"])
        docs = all_results.get("documents", [])
        metas = all_results.get("metadatas", [])

        matched = [
            (doc, meta) for doc, meta in zip(docs, metas)
            if start_iso <= meta.get("date", "") <= end_iso
        ]

        if matched:
            matched.sort(key=lambda p: p[1].get("date", ""))
            cap = effective_chunk_cap(question, n_results, force_max=force_max)
            results = matched[:cap] if len(matched) > cap else matched
            if diversify and len(results) > 3:
                # Keep the top 2 (most relevant by date proximity) then
                # shuffle the rest slightly so repeated questions vary.
                top = results[:2]
                rest = results[2:]
                random.shuffle(rest)
                return top + rest
            return results

    effective_n = effective_chunk_cap(question, n_results, force_max=force_max)
    # Fetch a slightly larger pool when diversifying, then sample from it
    # so the top result isn't always identical on repeated questions.
    fetch_n = min(effective_n * 2, effective_n + 6) if diversify else effective_n

    results = collection.query(query_texts=[question], n_results=fetch_n)
    docs = results.get("documents", [[]])[0]
    metas = results.get("metadatas", [[]])[0]
    pairs = list(zip(docs, metas))

    if diversify and len(pairs) > effective_n:
        # Always keep the single best hit, then sample the rest
        top = pairs[:1]
        pool = pairs[1:]
        sampled = random.sample(pool, min(effective_n - 1, len(pool)))
        return top + sampled

    return pairs


def retrieve_stale_context(collection, n_results=None):
    """
    Staleness query: pulls the oldest entries that have never been
    prominently surfaced. Sorts everything by date ascending and returns
    the earliest chunks -- these are the forgotten threads most likely
    to have never been revisited.
    """
    n_results = n_results or config.MAX_CONTEXT_CHUNKS
    all_results = collection.get(include=["documents", "metadatas"])
    docs = all_results.get("documents", [])
    metas = all_results.get("metadatas", [])

    pairs = [(doc, meta) for doc, meta in zip(docs, metas) if meta.get("date")]
    # Sort oldest first -- these are the buried, forgotten entries
    pairs.sort(key=lambda p: p[1].get("date", ""))
    # Sample from the oldest third rather than always returning the same
    # N entries -- adds variety across repeated staleness queries
    oldest_third = pairs[:max(n_results * 3, 30)]
    return random.sample(oldest_third, min(n_results, len(oldest_third)))


def retrieve_visual_context(question, text_collection, n_results=None):
    """
    Phase 4: finds journal photos by VISUAL content using CLIP.
    Returns list of (photo_filename, date, entry_text, score) tuples.
    Empty list = fall back to text search.
    """
    try:
        import image_embed
    except ImportError:
        return []

    if not image_embed.is_available():
        return []

    photo_collection = image_embed.get_photo_collection()
    if photo_collection.count() == 0:
        return []

    n_results = n_results or getattr(config, "MAX_VISUAL_RESULTS", 5)
    n_results = min(n_results, photo_collection.count())

    query_embedding = image_embed.embed_text_query(question)
    results = photo_collection.query(
        query_embeddings=[query_embedding],
        n_results=n_results,
        include=["metadatas", "distances"],
    )
    photo_ids = results.get("ids", [[]])[0]
    metadatas = results.get("metadatas", [[]])[0]
    distances = results.get("distances", [[]])[0]

    matches = []
    for photo_id, meta, distance in zip(photo_ids, metadatas, distances):
        # Convert distance to a 0-1 similarity score (CLIP uses cosine distance)
        score = 1.0 - distance
        entry_id = (meta or {}).get("entry_id") or ""
        date, text = "", ""
        if entry_id:
            entry_data = text_collection.get(where={"entry_id": entry_id})
            docs = entry_data.get("documents", [])
            metas_list = entry_data.get("metadatas", [])
            if docs:
                text = " ".join(docs)
            if metas_list:
                date = metas_list[0].get("date", "")
        matches.append((photo_id, date, text, score, entry_id))
    return matches


def get_all_known_tags(text_collection):
    """
    Scans every chunk's metadata once to build the set of all distinct tags
    ever extracted at ingest time (see ingest.py's extract_tags_batch).
    Used to figure out which tag (if any) a "give me everything about X"
    question is asking about -- matching against real tags rather than
    guessing at arbitrary topic keywords.
    """
    all_results = text_collection.get(include=["metadatas"])
    metas = all_results.get("metadatas", [])
    tags = set()
    for meta in metas:
        raw = (meta or {}).get("tags", "")
        if not raw:
            continue
        for t in raw.split(","):
            t = t.strip()
            if t:
                tags.add(t)
    return tags


def find_matching_tag(question, known_tags):
    """
    Returns the known tag that appears (case-insensitively, whole-word) in
    the question, or None if no real tag matches. Longest match wins when
    multiple tags match, so a more specific tag ("NAMI volunteering") beats
    a shorter substring ("NAMI") when both happen to be real tags.
    """
    q = question.lower()
    matches = []
    for tag in known_tags:
        tag_l = tag.lower().strip()
        if not tag_l:
            continue
        if re.search(rf"\b{re.escape(tag_l)}\b", q):
            matches.append(tag)
    if not matches:
        return None
    matches.sort(key=len, reverse=True)
    return matches[0]


def retrieve_tag_context(tag, collection, cap=None):
    """
    Full-scan retrieval of every chunk tagged with `tag` -- this is what
    guarantees completeness (every matching entry, not just the top-K most
    semantically similar) for "everything about X" style questions. Sorted
    oldest-to-newest so the result reads as a chronological history rather
    than similarity-ranked noise.
    """
    cap = cap or config.MAX_POWER_CONTEXT_CHUNKS
    all_results = collection.get(include=["documents", "metadatas"])
    docs = all_results.get("documents", [])
    metas = all_results.get("metadatas", [])

    tag_l = tag.lower()
    matched = []
    for doc, meta in zip(docs, metas):
        raw = (meta or {}).get("tags", "")
        if not raw:
            continue
        entry_tags = [t.strip().lower() for t in raw.split(",")]
        if tag_l in entry_tags:
            matched.append((doc, meta))

    matched.sort(key=lambda p: p[1].get("date", ""))
    return matched[:cap] if len(matched) > cap else matched


def retrieve_combined_context(question, text_collection, force_max=False):
    """
    Phase 5: runs text search and visual search in parallel, then merges
    results. Photos are only included if their CLIP similarity score clears
    a threshold -- so abstract questions ("what is my purpose") don't
    randomly pull in unrelated images. Returns a dict with 'chunks' (text),
    'photos' (visual matches above threshold), 'low_confidence' flag, and
    'matched_tag' (the tag this became a "Max Recall" completeness
    retrieval for, or None for a normal search).

    force_max=True comes from the chat UI's Max Recall toggle -- it forces
    tag-matching to run even on questions that don't look like a topic
    question by keyword, so the person can always get guaranteed-complete
    results on demand rather than relying only on auto-detection.
    """
    # Staleness questions get oldest entries, not most-relevant
    if is_staleness_question(question):
        stale_chunks = retrieve_stale_context(text_collection)
        return {"chunks": stale_chunks, "photos": [], "low_confidence": False, "matched_tag": None}

    # "Max Recall": if this looks like (or was forced to be) a "give me
    # everything about X" question, and X matches a real tag extracted at
    # ingest time, switch to a full-scan tag retrieval -- guaranteed to
    # include EVERY tagged entry, not just the top-K most semantically
    # similar ones. Falls through to normal search below if no tag matches.
    matched_tag = None
    if force_max or is_topic_question(question):
        known_tags = get_all_known_tags(text_collection)
        matched_tag = find_matching_tag(question, known_tags)

    if matched_tag:
        text_chunks = retrieve_tag_context(matched_tag, text_collection)
        low_confidence = False
    else:
        # Always get text results
        diversify = _is_diversity_question(question)
        text_chunks = retrieve_context(
            question, text_collection, diversify=diversify, force_max=force_max
        )
        low_confidence = not check_relevance_confidence(question, text_chunks)

    # Skip visual search for questions where photos would be noise
    if should_suppress_photos(question) or not _visual_available():
        return {"chunks": text_chunks, "photos": [], "low_confidence": low_confidence, "matched_tag": matched_tag}

    try:
        import image_embed
        photo_collection = image_embed.get_photo_collection()
        if photo_collection.count() == 0:
            return {"chunks": text_chunks, "photos": [], "low_confidence": low_confidence, "matched_tag": matched_tag}

        threshold = getattr(config, "PHASE5_VISUAL_THRESHOLD", 0.20)

        # ENTRY-BASED PHOTO MATCHING: For each retrieved text entry (in order),
        # find photos tied to that specific entry. Order matters!
        photos = []
        photos_by_entry = {}  # Map entry_id -> list of (photo_id, meta)
        retrieved_entry_ids = set()  # Track which entries were retrieved
        
        # First pass: collect all entry IDs in order and fetch their photos
        for _, meta in text_chunks:
            entry_id = meta.get("entry_id")
            retrieved_entry_ids.add(entry_id) if entry_id else None
            if entry_id and entry_id not in photos_by_entry:
                try:
                    # Fetch all photos for this specific entry
                    entry_photos = photo_collection.get(
                        where={"entry_id": entry_id},
                        include=["metadatas"]
                    )
                    photo_ids_for_entry = entry_photos.get("ids", [])
                    metadatas_for_entry = entry_photos.get("metadatas", [])
                    
                    if photo_ids_for_entry:
                        photos_by_entry[entry_id] = list(zip(photo_ids_for_entry, metadatas_for_entry))
                except Exception:
                    photos_by_entry[entry_id] = []
        
        # Second pass: iterate through text_chunks IN ORDER and add their photos IN ORDER
        for _, meta in text_chunks:
            entry_id = meta.get("entry_id")
            if entry_id in photos_by_entry:
                # This entry has photos - add ALL of them (or sample for diversity)
                entry_photos_list = photos_by_entry[entry_id]
                
                if entry_photos_list:
                    # For diversity questions, potentially use multiple photos per entry
                    # For regular questions, use all available photos (don't skip)
                    for idx, (photo_id, photo_meta) in enumerate(entry_photos_list):
                        # High confidence score since it's directly tied to this entry
                        score = 0.95 - (idx * 0.05)  # First: 0.95, second: 0.90, etc.
                        if score >= threshold:
                            # Get the entry date and text for context
                            entry_data = text_collection.get(where={"entry_id": entry_id})
                            docs = entry_data.get("documents", [])
                            metas_list = entry_data.get("metadatas", [])
                            date = metas_list[0].get("date", "") if metas_list else ""
                            text = " ".join(docs) if docs else ""
                            
                            photos.append((photo_id, date, text, score, entry_id))
        
        # Apply diversity sampling if question asks for it and we have extras
        # Uses config settings to determine when to sample and how many to cap at
        diversity_threshold = getattr(config, "DIVERSITY_ENTRY_THRESHOLD", 3)
        diversity_max_entries = getattr(config, "DIVERSITY_MAX_ENTRIES", None)
        
        # Count entries that have photos for this sampling decision
        entries_with_photos = len([e for e in retrieved_entry_ids if e in photos_by_entry and photos_by_entry[e]])
        
        if _is_diversity_question(question) and entries_with_photos >= diversity_threshold and len(photos) > diversity_threshold:
            # Apply diversity sampling: keep top entries, randomly sample the rest
            # This makes repeated "give me more" questions show different entries.
            #
            # IMPORTANT: random.sample() picks *which* items to keep, but the
            # order it returns them in is randomized -- not their original
            # position. Since the text excerpts keep their own separate order
            # (built from text_chunks, not from `photos`), sampling this list
            # directly caused the photo strip and the text list to drift out
            # of sync (e.g. the oldest/first excerpt no longer matching the
            # first photo shown). Fix: sample *indices*, then sort those
            # indices before rebuilding the list, so randomness only picks
            # which photos survive -- not what order they end up in.
            top_count = min(2, len(photos))  # Keep top 2 as anchors
            kept = photos[:top_count]
            pool = photos[top_count:]
            # Sample about half of the remaining for good variety
            sample_size = min(len(pool) // 2, len(pool))
            if sample_size > 0:
                sampled_indices = sorted(random.sample(range(len(pool)), sample_size))
                sampled = [pool[i] for i in sampled_indices]
            else:
                sampled = []
            photos = kept + sampled
        
        # Cap entries if configured
        if diversity_max_entries and len(photos) > diversity_max_entries:
            photos = photos[:diversity_max_entries]

        return {"chunks": text_chunks, "photos": photos, "low_confidence": low_confidence, "matched_tag": matched_tag}

    except Exception:
        return {"chunks": text_chunks, "photos": [], "low_confidence": low_confidence, "matched_tag": matched_tag}


def _is_diversity_question(question):
    """
    Questions phrased as "give me a time when..." or "when did I ever..."
    explicitly want variety, not just the single best match every time.
    Also includes reflective questions like "give me a reflection" that
    benefit from varied results on repeated asks.
    """
    q = question.lower()
    diversity_phrases = (
        "give me a time", "a time when", "an example of", "when did i",
        "when have i", "find a moment", "one time", "any time i",
        "give me a reflection", "give me more", "tell me more",
        "another", "more of a", "what else", "any other",
    )
    return any(p in q for p in diversity_phrases)


def _visual_available():
    try:
        import image_embed
        return image_embed.is_available()
    except ImportError:
        return False


def check_relevance_confidence(question, context_chunks):
    """
    Rough heuristic: if the top retrieved chunks share very little
    lexical overlap with the question, the journal probably doesn't
    contain anything relevant. Returns False when confidence is low,
    so we can respond with "I don't see anything about that" instead
    of hallucinating relevance from unrelated entries.

    This is intentionally simple -- a keyword overlap check, not a
    learned model -- so it's fast, free, and doesn't add API calls.
    """
    if not context_chunks:
        return False

    # Extract meaningful words from the question (skip stop words)
    stop_words = {
        "a", "an", "the", "is", "are", "was", "were", "be", "been",
        "have", "has", "had", "do", "does", "did", "will", "would",
        "could", "should", "may", "might", "i", "me", "my", "what",
        "when", "where", "how", "why", "who", "about", "tell", "show",
        "find", "give", "any", "ever", "time", "did", "in", "on",
        "at", "to", "of", "for", "with", "and", "or", "that", "this",
    }
    q_words = {
        w for w in re.findall(r"[a-z]+", question.lower())
        if w not in stop_words and len(w) > 3
    }

    if not q_words:
        return True  # Can't check -- let it through

    # Check overlap across all returned chunks
    all_chunk_text = " ".join(doc for doc, _ in context_chunks).lower()
    chunk_words = set(re.findall(r"[a-z]+", all_chunk_text))
    overlap = q_words & chunk_words
    overlap_ratio = len(overlap) / len(q_words)

    # If fewer than 20% of meaningful question words appear anywhere
    # in the retrieved chunks, confidence is too low to answer well.
    return overlap_ratio >= 0.20


def build_no_match_prompt(question):
    """Prompt for when retrieved chunks have low confidence for the question."""
    return (
        "The person asked their journal a question, but the journal doesn't "
        "appear to contain relevant entries about this topic. Respond briefly "
        "(1-2 sentences) letting them know you don't see relevant entries, "
        "and suggest they might want to write about this topic or rephrase "
        "the question. Be warm, not robotic. Then stop.\n\n"
        f"Question: {question}"
    )


def build_statement_prompt(statement):
    """
    Builds a prompt for when the person typed a statement instead of a
    question -- acknowledges it warmly and invites them to explore related
    journal entries if they want.
    """
    prompt = (
        "The person shared a personal statement rather than asking a question. "
        "Respond warmly and briefly (2-3 sentences) -- acknowledge what they said "
        "and gently suggest they could ask you to find related journal entries if "
        "they're curious. Don't be overly effusive. Then stop.\n\n"
        f"Statement: {statement}"
    )
    return prompt


def build_visual_prompt(question, visual_matches):
    context_block = "\n\n".join(
        f"[{date or 'unknown date'}] Photo file: {photo_id}\n"
        f"Entry text: {text or '(no entry text available for this photo)'}"
        for photo_id, date, text, *_ in visual_matches
    )
    prompt = (
        "The person searched their journal photos visually. A CLIP image-similarity "
        "search has already found the closest matching photos -- treat these as the "
        "correct visual matches, even if the journal entry text doesn't explicitly "
        "describe the photo's content (entry text is often about what the person was "
        "doing or thinking, not a caption of the photo itself).\n\n"
        "Your job: write 1-2 sentences of context about when/where this was from "
        "using the entry text, then confirm these are the closest matches found. "
        "Do NOT say the photo doesn't match or express doubt about the visual search "
        "results -- the image similarity model already handled that. "
        "Then stop -- no closing question.\n\n"
        f"--- Matched photos ---\n{context_block}\n--- End matches ---\n\n"
        f"Question: {question}"
    )
    return prompt


def build_combined_prompt(question, combined):
    """
    Phase 5: builds a prompt that weaves together text chunks and any
    relevant photos into a single coherent context block for Claude.
    Routes to a "no match" response when confidence is low.
    """
    if combined.get("low_confidence"):
        return build_no_match_prompt(question)

    matched_tag = combined.get("matched_tag")
    chunks = combined["chunks"]
    photos = combined["photos"]

    text_block = "\n\n".join(
        f"[{meta.get('date', 'unknown date')}] {doc}"
        for doc, meta in chunks
    )

    if not photos:
        # No relevant photos -- fall through to standard text prompt
        return build_prompt(question, chunks, matched_tag=matched_tag)

    photo_block = "\n\n".join(
        f"[{date or 'unknown date'}] 📸 Photo: {photo_id} (relevance: {score:.0%})\n"
        f"Entry context: {text[:300] + '...' if len(text) > 300 else text or '(none)'}"
        for photo_id, date, text, score, *_ in photos
    )

    if matched_tag:
        prompt = (
            f"Below is EVERY journal excerpt tagged '{matched_tag}' (not just "
            "the most relevant few -- the person asked for completeness via "
            "Max Recall), along with any related photos. Give a thorough "
            "answer: a short overview, then the relevant details, organized "
            "chronologically or thematically. If there are related photos, "
            "mention them naturally (e.g. 'there's also a photo from this "
            "day') -- don't list filenames, the UI will display them. Then "
            "stop -- no closing question.\n\n"
            f"--- All journal excerpts tagged '{matched_tag}' ---\n{text_block}\n--- End excerpts ---\n\n"
            f"--- Related photos ---\n{photo_block}\n--- End photos ---\n\n"
            f"Question: {question}"
        )
        return prompt

    if is_synthesis_question(question):
        prompt = (
            "The person wants these journal excerpts synthesized. Do two things:\n"
            "1. A short synthesis (3-5 sentences) combining what's happening across "
            "entries into a coherent picture.\n"
            "2. A concrete 'Action items' section: specific next steps implied by "
            "the entries. Only include real ones -- if there aren't any, say so.\n"
            "Then stop -- no closing question. Photos below are included for context "
            "but focus synthesis on the text entries.\n\n"
            f"--- Journal excerpts ---\n{text_block}\n--- End excerpts ---\n\n"
            f"--- Related photos ---\n{photo_block}\n--- End photos ---\n\n"
            f"Question: {question}"
        )
        return prompt

    prompt = (
        "Give a brief summary (2-3 sentences) of your finding, then list the related "
        "journal excerpts with their dates. If there are related photos listed below, "
        "mention them naturally (e.g. 'there's also a photo from this day') -- "
        "don't list filenames, the UI will display them. Then stop -- no closing question.\n\n"
        f"--- Journal excerpts ---\n{text_block}\n--- End excerpts ---\n\n"
        f"--- Related photos (mention but don't list filenames) ---\n{photo_block}\n--- End photos ---\n\n"
        f"Question: {question}"
    )
    return prompt


def build_prompt(question, context_chunks, matched_tag=None):
    context_block = "\n\n".join(
        f"[{meta.get('date', 'unknown date')}] {doc}"
        for doc, meta in context_chunks
    )

    if matched_tag:
        prompt = (
            f"Below is EVERY journal excerpt tagged '{matched_tag}' -- not "
            "just the most relevant few, all of them, because the person "
            "asked for completeness (Max Recall). Give a thorough answer "
            "that draws on the full set: a short overview, then the "
            "relevant details, organized chronologically or thematically "
            "as makes sense. Then stop -- no closing question. Use only "
            "the excerpts below; don't pull in outside information unless "
            "asked.\n\n"
            f"--- All journal excerpts tagged '{matched_tag}' ---\n{context_block}\n--- End excerpts ---\n\n"
            f"Question: {question}"
        )
        return prompt

    if is_synthesis_question(question):
        prompt = (
            "The person wants these journal excerpts actively synthesized, "
            "not just listed. Do two things:\n"
            "1. A short synthesis (3-5 sentences) that combines what's "
            "happening across these entries into a coherent picture -- "
            "connect related items across different days rather than "
            "summarizing each day separately.\n"
            "2. A concrete 'Action items' section: a bullet list of "
            "specific next steps or follow-ups implied by these entries. "
            "Only include real action items actually implied by the "
            "content -- if there genuinely aren't any, say so rather than "
            "inventing generic ones.\n"
            "Then stop -- no closing question. Use only the excerpts "
            "below; don't pull in outside information unless asked.\n\n"
            f"--- Journal excerpts ---\n{context_block}\n--- End excerpts ---\n\n"
            f"Question: {question}"
        )
        return prompt

    prompt = (
        "Give a brief summary (2-3 sentences) of your finding, then list "
        "the related journal excerpts with their dates. Then stop -- no "
        "closing question. Use only the excerpts below; don't pull in "
        "outside information unless asked.\n\n"
        f"--- Journal excerpts ---\n{context_block}\n--- End excerpts ---\n\n"
        f"Question: {question}"
    )
    return prompt


def main():
    if len(sys.argv) < 2:
        print('Usage: python ask.py "your question here"')
        return

    question = sys.argv[1]

    if not config.ANTHROPIC_API_KEY:
        print("Set the ANTHROPIC_API_KEY environment variable first.")
        return

    client = chromadb.PersistentClient(path=config.VECTOR_DB_DIR)
    collection = client.get_or_create_collection("journal")

    if collection.count() == 0:
        print("Your journal DB is empty -- run ingest.py first.")
        return

    anthropic_client = Anthropic(api_key=config.ANTHROPIC_API_KEY)

    # Statement detection -- respond warmly instead of doing a confused search
    if is_statement(question):
        prompt = build_statement_prompt(question)
        response = anthropic_client.messages.create(
            model=config.CLAUDE_MODEL,
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )
        answer = "".join(
            block.text for block in response.content if hasattr(block, "text")
        )
        print(answer)
        return

    # Phase 4: explicit visual search questions (still routes to visual-only)
    if is_visual_search_question(question):
        visual_matches = retrieve_visual_context(question, collection)
        if visual_matches:
            prompt = build_visual_prompt(question, visual_matches)
            response = anthropic_client.messages.create(
                model=config.CLAUDE_MODEL,
                max_tokens=1000,
                messages=[{"role": "user", "content": prompt}],
            )
            answer = "".join(
                block.text for block in response.content if hasattr(block, "text")
            )
            print(answer)
            print("\n📸 Matched photo(s):")
            for photo_id, date, _, *__ in visual_matches:
                print(f"  [{date or 'unknown date'}] {photo_id}")
            usage = getattr(response, "usage", None)
            if usage:
                print(f"\n(~{usage.input_tokens} input / {usage.output_tokens} output tokens)")
            return

    # Phase 5: combined text + visual search for all other questions
    combined = retrieve_combined_context(question, collection)
    prompt = build_combined_prompt(question, combined)

    est_tokens = rough_token_estimate(prompt)
    if est_tokens > config.MAX_TOKENS_PER_QUERY_WARNING:
        print(f"⚠️  Large query (~{est_tokens} tokens). Consider lowering "
              f"MAX_CONTEXT_CHUNKS in config.py if this becomes frequent.\n")

    response = anthropic_client.messages.create(
        model=config.CLAUDE_MODEL,
        max_tokens=1000,
        messages=[{"role": "user", "content": prompt}],
    )

    answer = "".join(
        block.text for block in response.content if hasattr(block, "text")
    )
    print(answer)

    if combined["photos"]:
        print("\n📸 Related photo(s) found:")
        for photo_id, date, _, score, *__ in combined["photos"]:
            print(f"  [{date or 'unknown date'}] {photo_id} ({score:.0%} match)")

    usage = getattr(response, "usage", None)
    if usage:
        print(f"\n(~{usage.input_tokens} input / {usage.output_tokens} output tokens)")


if __name__ == "__main__":
    main()