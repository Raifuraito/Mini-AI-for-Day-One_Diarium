"""
Ingests journal export files (JSON, or a Day One JSON-zip export) into
a local vector DB.

Supports:
  - Diarium JSON export format
  - Day One JSON export format (raw .json, or the .zip Day One produces)
  - A generic fallback format: [{"date": "...", "text": "..."}]

Only processes entries that are new or changed since the last run,
tracked via PROCESSED_LOG, to avoid re-embedding (and re-paying for)
the whole journal every time.

Usage:
    python ingest.py path/to/export.json
    python ingest.py path/to/export.zip
    python ingest.py               # scans EXPORT_WATCH_DIR for *.json and *.zip
"""

import json
import os
import sys
import hashlib
import glob
import zipfile
import shutil
import re
from datetime import datetime

import chromadb

import config

# --- Sentiment scoring (keyword-ratio based, no external library) ---
# Deliberately simple: counts word-boundary matches against two fixed
# lists and returns a ratio, rather than pulling in a model/library.
# Journal-tuned, not general-purpose -- lean toward words people actually
# use to describe how a day/entry felt, not formal sentiment-corpus words.
POSITIVE_KEYWORDS = (
    "happy", "grateful", "great", "good", "excited", "love", "loved",
    "joy", "joyful", "proud", "peaceful", "calm", "relieved", "hopeful",
    "hope", "content", "amazing", "wonderful", "fun", "enjoyed", "enjoy",
    "accomplished", "confident", "energized", "relaxed", "thankful",
    "beautiful", "delighted", "optimistic", "motivated", "satisfied",
    "success", "successful", "laughed", "laughing", "smile", "smiled",
    "blessed", "fantastic", "excellent", "lucky",
)

NEGATIVE_KEYWORDS = (
    "sad", "angry", "anxious", "anxiety", "stressed", "stress", "tired",
    "exhausted", "frustrated", "frustrating", "worried", "worry", "upset",
    "depressed", "depression", "lonely", "alone", "hurt", "afraid",
    "scared", "fear", "annoyed", "irritated", "overwhelmed", "hopeless",
    "disappointed", "disappointing", "guilty", "ashamed", "regret",
    "awful", "terrible", "horrible", "bad", "cried", "crying", "fight",
    "argument", "failed", "failure", "hate", "hated", "miserable",
)


def score_sentiment(text):
    """
    Keyword-ratio sentiment score for a chunk of journal text.

    Counts word-boundary matches against POSITIVE_KEYWORDS and
    NEGATIVE_KEYWORDS (case-insensitive) and returns
    (pos_count - neg_count) / (pos_count + neg_count), clamped to
    [-1.0, 1.0]. Returns 0.0 (neutral) when there are no matches at all --
    this is a "nothing detected" default, not a claim the entry is
    emotionally neutral.
    """
    if not text:
        return 0.0

    text_lower = text.lower()
    pos_count = sum(
        len(re.findall(rf"\b{re.escape(word)}\b", text_lower))
        for word in POSITIVE_KEYWORDS
    )
    neg_count = sum(
        len(re.findall(rf"\b{re.escape(word)}\b", text_lower))
        for word in NEGATIVE_KEYWORDS
    )

    total = pos_count + neg_count
    if total == 0:
        return 0.0

    score = (pos_count - neg_count) / total
    return max(-1.0, min(1.0, score))

# Where extracted zip contents go -- kept separate from the watch folder
# so re-scans don't pick up our own extracted files as new "exports".
# Anchored to this file's own location -- never depends on the working
# directory at launch time, so moving or running the project from a
# different folder (e.g. starting server.py from inside webapp/) always
# resolves PHOTOS_DIR to the same real place on disk.
_PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
EXTRACT_DIR = os.path.join(_PROJECT_ROOT, "_extracted_exports")

# Where extracted photos permanently live -- this is what webapp/server.py
# serves from. Separate from EXTRACT_DIR (which is just a scratch space
# re-used/overwritten each ingest run) since photos need to persist.
PHOTOS_DIR = os.path.join(_PROJECT_ROOT, "photos")


def load_processed_log():
    if os.path.exists(config.PROCESSED_LOG):
        with open(config.PROCESSED_LOG, "r") as f:
            return json.load(f)
    return {}


def save_processed_log(log):
    with open(config.PROCESSED_LOG, "w") as f:
        json.dump(log, f, indent=2)


def extract_json_from_zip(zip_path):
    """
    Extracts the first .json file found inside a Day One export zip,
    returning its path. Also extracts any photos/ folder present (only
    relevant if the export was made with "Include media") into the
    permanent PHOTOS_DIR, deduplicating by filename so re-running this
    on the same zip doesn't re-copy files that already exist locally.
    """
    os.makedirs(EXTRACT_DIR, exist_ok=True)
    with zipfile.ZipFile(zip_path, "r") as z:
        json_names = [n for n in z.namelist() if n.lower().endswith(".json")]
        if not json_names:
            raise ValueError(f"No .json file found inside {zip_path}")
        # Day One puts one journal JSON per zip; take the first/only one.
        json_name = json_names[0]
        z.extract(json_name, EXTRACT_DIR)
        extracted_path = os.path.join(EXTRACT_DIR, json_name)
        # Flatten in case it was nested in a subfolder inside the zip.
        flat_path = os.path.join(EXTRACT_DIR, os.path.basename(json_name))
        if extracted_path != flat_path:
            shutil.move(extracted_path, flat_path)

        # Photos: Day One's "Include media" export puts image files under
        # a photos/ (or similar) folder inside the zip. Extract any image
        # files found anywhere in the archive into PHOTOS_DIR, flattening
        # paths and skipping files that already exist locally (cheap,
        # avoids redundant disk writes on repeat full-journal exports).
        os.makedirs(PHOTOS_DIR, exist_ok=True)
        image_exts = (".jpg", ".jpeg", ".png", ".heic", ".gif", ".webp")
        photo_names = [n for n in z.namelist() if n.lower().endswith(image_exts)]
        extracted_photo_count = 0
        for name in photo_names:
            dest_name = os.path.basename(name)
            dest_path = os.path.join(PHOTOS_DIR, dest_name)
            if os.path.exists(dest_path):
                continue  # already have this photo, skip
            with z.open(name) as src, open(dest_path, "wb") as dst:
                shutil.copyfileobj(src, dst)
            extracted_photo_count += 1
        if photo_names:
            print(f"  -> {extracted_photo_count} new photo(s) extracted "
                  f"({len(photo_names) - extracted_photo_count} already present).")

        return flat_path


def resolve_to_json(filepath):
    """Given a .json or .zip path, returns a usable .json path."""
    if filepath.lower().endswith(".zip"):
        return extract_json_from_zip(filepath)
    return filepath


def embed_new_photos(entry_photo_map=None):
    """
    Phase 4: embeds any photo in PHOTOS_DIR that isn't already in the
    journal_photos Chroma collection, using CLIP (see image_embed.py).

    Kept as a standalone pass (not woven into the text-ingestion loop)
    so it can be skipped entirely if image_embed isn't available (no
    internet on first run, etc.) without breaking Phases 1-3 at all.

    entry_photo_map: optional dict of {photo_filename: entry_id} so each
    embedded photo can be traced back to which journal entry it came
    from -- used later to show the entry's date/text alongside a visual
    search result, not just a bare photo.
    """
    try:
        import image_embed
    except ImportError:
        print("image_embed module not found -- skipping photo embedding "
              "(Phase 4 visual search will be unavailable, Phases 1-3 unaffected).")
        return 0

    if not image_embed.is_available():
        print("CLIP model unavailable (no internet on first run, or download "
              "failed) -- skipping photo embedding this run. Will retry next "
              "run automatically; Phases 1-3 are unaffected.")
        return 0

    if not os.path.isdir(PHOTOS_DIR):
        return 0

    collection = image_embed.get_photo_collection()
    existing_ids = set(collection.get(include=[])["ids"]) if collection.count() > 0 else set()

    entry_photo_map = entry_photo_map or {}
    image_exts = (".jpg", ".jpeg", ".png", ".heic", ".gif", ".webp")
    all_photos = [
        f for f in os.listdir(PHOTOS_DIR)
        if f.lower().endswith(image_exts)
    ]
    new_photos = [f for f in all_photos if f not in existing_ids]

    # Backfill pass: photos already embedded in a prior run may have been
    # linked with a blank entry_id (e.g. their source entry was already
    # marked "processed" that run, so the mapping loop never touched them --
    # see main()). If entry_photo_map now has a real entry_id for one of
    # these, patch just its metadata -- no re-embedding, no CLIP call.
    if existing_ids and entry_photo_map:
        backfillable = [f for f in existing_ids if f in entry_photo_map]
        if backfillable:
            existing_meta = collection.get(ids=backfillable, include=["metadatas"])
            to_update_ids, to_update_metas = [], []
            for photo_id, meta in zip(existing_meta["ids"], existing_meta["metadatas"]):
                current_entry_id = (meta or {}).get("entry_id") or ""
                new_entry_id = entry_photo_map.get(photo_id, "")
                if not current_entry_id and new_entry_id:
                    to_update_ids.append(photo_id)
                    to_update_metas.append({"entry_id": new_entry_id})
            if to_update_ids:
                collection.update(ids=to_update_ids, metadatas=to_update_metas)
                print(f"  -> Backfilled entry_id for {len(to_update_ids)} "
                      f"already-embedded photo(s) (metadata only, no re-embed).")

    if not new_photos:
        print(f"  -> No new photos to embed ({len(all_photos)} already "
              f"embedded for visual search).")
        return 0

    embedded_count = 0
    for filename in new_photos:
        photo_path = os.path.join(PHOTOS_DIR, filename)
        try:
            embedding = image_embed.embed_image(photo_path)
        except Exception as e:
            print(f"  -> Skipped {filename} (couldn't embed: {e})")
            continue
        collection.add(
            ids=[filename],
            embeddings=[embedding],
            metadatas=[{"entry_id": entry_photo_map.get(filename, "")}],
        )
        embedded_count += 1

    if embedded_count:
        print(f"  -> Embedded {embedded_count} new photo(s) for visual search "
              f"({len(new_photos) - embedded_count} skipped due to errors).")
    return embedded_count


def entry_hash(entry_id, text):
    """Hash content so we detect edits to existing entries, not just new ones."""
    return hashlib.sha256(f"{entry_id}:{text}".encode("utf-8")).hexdigest()


def _find_photo_on_disk(identifier):
    """
    Looks for a file in PHOTOS_DIR whose name starts with `identifier`
    (case-insensitive). Day One exports name photos by their md5 or
    identifier, but the extension varies (.jpeg, .jpg, .png, .heic, etc.)
    and isn't always predictable from the JSON metadata alone. Scanning
    the folder directly is more reliable than guessing.

    Returns the matching filename (just the basename), or None if not found.
    """
    if not identifier or not os.path.isdir(PHOTOS_DIR):
        return None
    identifier_lower = identifier.lower()
    try:
        for fname in os.listdir(PHOTOS_DIR):
            if fname.lower().startswith(identifier_lower):
                return fname
    except OSError:
        pass
    return None


def extract_photo_filename(entry):
    """
    Day One entries can reference photos two ways depending on export
    version: a top-level "photos" array with identifier/md5/type fields,
    or embedded objects inside the richText JSON. This checks both and
    returns the actual filename found on disk in PHOTOS_DIR (not a guess),
    so the name always matches a real file the web server can serve.
    """
    photos = entry.get("photos")
    if photos:
        p = photos[0]  # just the first attached photo for now
        # Try md5 first (used as the actual filename in most Day One exports),
        # then fall back to identifier.
        for key in ("md5", "identifier"):
            candidate = p.get(key)
            if candidate:
                found = _find_photo_on_disk(candidate)
                if found:
                    return found
                # If not on disk yet (photos not extracted yet), fall back to
                # constructing the name -- it'll be correct once the zip is
                # extracted and the file lands in PHOTOS_DIR.
                ext = p.get("type", "jpeg")
                return f"{candidate}.{ext}"

    # Fallback: look for an embedded photo/media object inside richText.
    rich_text = entry.get("richText", "")
    if rich_text:
        match = re.search(
            r'"type":"photo"[^}]*"identifier":"([A-F0-9-]+)"',
            rich_text, re.IGNORECASE,
        )
        if match:
            identifier = match.group(1)
            found = _find_photo_on_disk(identifier)
            if found:
                return found
            return f"{identifier}.jpeg"

    return None


def normalize_entries(raw_data):
    """
    Best-effort normalization across export formats.
    Returns a list of dicts: {"id": str, "date": str, "text": str}
    """
    entries = []

    # Day One export format: {"entries": [{"uuid": ..., "text": ..., "creationDate": ...}]}
    if isinstance(raw_data, dict) and "entries" in raw_data:
        for e in raw_data["entries"]:
            # creationDate is the entry's actual date (Day One lets you
            # manually correct this if you post late, so it's trustworthy).
            # Only fall back to modifiedDate if creationDate is truly
            # missing -- never grab an unrelated nested "date" (e.g. from
            # embedded location/weather/media objects), since that's what
            # caused entries to get mismatched dates previously.
            entry_date = e.get("creationDate") or e.get("modifiedDate") or ""
            entries.append({
                "id": e.get("uuid") or e.get("id"),
                "date": entry_date,
                "text": e.get("text", "").strip(),
                "photo": extract_photo_filename(e),
            })
        return entries

    # Diarium JSON export: often a flat list or {"Entries": [...]}
    if isinstance(raw_data, dict) and "Entries" in raw_data:
        for e in raw_data["Entries"]:
            entries.append({
                "id": e.get("Id") or e.get("Date"),
                "date": e.get("Date", ""),
                "text": (e.get("Text") or e.get("Content") or "").strip(),
                "photo": None,
            })
        return entries

    # Generic fallback: flat list of {"date"/"text"} or similar
    if isinstance(raw_data, list):
        for e in raw_data:
            entries.append({
                "id": e.get("id") or e.get("date"),
                "date": e.get("date", ""),
                "text": (e.get("text") or e.get("content") or "").strip(),
                "photo": None,
            })
        return entries

    raise ValueError("Unrecognized export format -- check the JSON structure.")


def chunk_text(text, size=None, overlap=None):
    size = size or config.CHUNK_SIZE_CHARS
    overlap = overlap or config.CHUNK_OVERLAP_CHARS
    if len(text) <= size:
        return [text]
    chunks = []
    start = 0
    while start < len(text):
        end = start + size
        chunks.append(text[start:end])
        start = end - overlap
    return chunks


_tagging_client = None


def get_tagging_client():
    """
    Lazily-created Anthropic client used only for tag extraction. Separate
    from any client server.py/ask.py create -- this module can be imported
    (e.g. by server.py, for PHOTOS_DIR) without requiring an API key unless
    tagging actually runs.
    """
    global _tagging_client
    if _tagging_client is None:
        from anthropic import Anthropic
        _tagging_client = Anthropic(api_key=config.ANTHROPIC_API_KEY)
    return _tagging_client


def extract_tags_batch(entries, client):
    """
    Extracts tags (people/places/organizations/themes) for a batch of
    entries in ONE Claude call instead of one call per entry -- with
    1000+ entries in a typical journal, calling once per entry would mean
    1000+ separate API round trips on a --force backfill. Batching by
    config.TAG_BATCH_SIZE cuts that dramatically.

    `entries` is a list of (entry_id, text) tuples. Returns a dict of
    {entry_id: tag_string}; an id missing from the result (or an empty
    tag_string) just means "no tags for that entry" -- callers should
    treat that as a normal outcome, not an error, since a missing tag is
    a worse *answer* later, not a broken ingest run now.
    """
    if not entries:
        return {}

    snippet_chars = getattr(config, "TAG_SNIPPET_CHARS", 800)
    numbered = "\n\n".join(
        f"[{i}] (id={eid})\n{text[:snippet_chars]}"
        for i, (eid, text) in enumerate(entries)
    )
    prompt = (
        "For each numbered journal entry below, extract a short list of "
        "tags: notable people (first names/nicknames as written), places "
        "or organizations, and 1-3 broad themes. Output ONLY a JSON object "
        "mapping each entry's number (as a string) to an array of tag "
        "strings -- no other text, no explanation, no markdown code fence. "
        "Omit entries with nothing notable rather than guessing. Example:\n"
        '{"0": ["NAMI", "Jonathan", "mental health advocacy"], "2": ["California trip"]}'
        f"\n\n{numbered}"
    )
    try:
        response = client.messages.create(
            model=config.TAG_EXTRACTION_MODEL,
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = "".join(b.text for b in response.content if hasattr(b, "text")).strip()
        # Claude sometimes wraps JSON in a code fence despite instructions
        # not to -- strip that off rather than failing the whole batch.
        if raw.startswith("```"):
            raw = raw.strip("`")
            if raw.lower().startswith("json"):
                raw = raw[4:]
            raw = raw.strip()
        parsed = json.loads(raw)
        result = {}
        for i, (eid, _) in enumerate(entries):
            tags = parsed.get(str(i))
            if tags:
                result[eid] = ", ".join(str(t).strip() for t in tags if str(t).strip())
        return result
    except Exception as ex:
        print(f"  -> Tag extraction failed for a batch of {len(entries)} "
              f"entries (non-fatal, those entries just get no tags): {ex}")
        return {}


def ingest_file(filepath, collection, processed_log, entry_photo_map=None):
    json_path = resolve_to_json(filepath)
    with open(json_path, "r", encoding="utf-8") as f:
        raw_data = json.load(f)

    entries = normalize_entries(raw_data)

    # Phase 4: record {photo_filename: entry_id} for every entry that has a
    # photo, not just new/changed ones. This runs over ALL entries (not the
    # new_or_changed filter below) so that photos extracted in a prior run --
    # before Phase 4 existed -- still get a correct entry_id mapping the
    # first time embed_new_photos() looks at them.
    if entry_photo_map is not None:
        for e in entries:
            if e.get("photo"):
                entry_photo_map[e["photo"]] = str(e["id"])

    new_or_changed = []
    for e in entries:
        if not e["text"]:
            continue
        # Include photo reference in the hash so a photo being added/removed
        # from an otherwise-unchanged entry still triggers a re-embed.
        h = entry_hash(e["id"], e["text"] + str(e.get("photo") or ""))
        if processed_log.get(str(e["id"])) == h:
            continue  # unchanged, skip -- this is the cost guardrail
        new_or_changed.append((e, h))

    if not new_or_changed:
        print(f"[{filepath}] No new or changed entries. Nothing to embed.")
        return 0

    # Extract tags (people/places/themes) for every new/changed entry,
    # batched TAG_BATCH_SIZE at a time -- see extract_tags_batch() above
    # for why this is batched instead of one call per entry. Runs BEFORE
    # the chunk-building loop below since tags are per-entry, not per-chunk,
    # and every chunk of an entry shares the same tag string.
    entry_tags = {}
    tag_batch = []
    tagging_client = get_tagging_client()
    for e, h in new_or_changed:
        if e["text"]:
            tag_batch.append((str(e["id"]), e["text"]))
        if len(tag_batch) >= config.TAG_BATCH_SIZE:
            entry_tags.update(extract_tags_batch(tag_batch, tagging_client))
            tag_batch = []
    if tag_batch:
        entry_tags.update(extract_tags_batch(tag_batch, tagging_client))

    ids, docs, metadatas = [], [], []
    for e, h in new_or_changed:
        tags = entry_tags.get(str(e["id"]), "")
        for i, chunk in enumerate(chunk_text(e["text"])):
            ids.append(f"{e['id']}_{i}")
            docs.append(chunk)
            metadatas.append({
                "date": e["date"],
                "entry_id": str(e["id"]),
                "photo": e.get("photo") or "",
                "sentiment": score_sentiment(chunk),
                "tags": tags,
            })
        processed_log[str(e["id"])] = h

    collection.upsert(ids=ids, documents=docs, metadatas=metadatas)
    print(f"[{filepath}] Embedded {len(new_or_changed)} new/changed entries "
          f"({len(ids)} chunks).")
    return len(new_or_changed)


def main():
    os.makedirs(config.VECTOR_DB_DIR, exist_ok=True)
    client = chromadb.PersistentClient(path=config.VECTOR_DB_DIR)
    collection = client.get_or_create_collection("journal")

    processed_log = load_processed_log()
    file_mtimes = processed_log.setdefault("_file_mtimes", {})

    # --force clears the processed-entry cache so everything re-embeds from
    # scratch. Use this when you've changed what gets stored in metadata
    # (e.g. adding sentiment scores) and need to backfill existing entries.
    force = "--force" in sys.argv
    if force:
        print("--force flag detected: clearing processed entry cache and re-embedding everything.")
        keys_to_clear = [k for k in processed_log if not k.startswith("_")]
        for k in keys_to_clear:
            del processed_log[k]
        # Also clear file mtimes so the mtime check doesn't skip files
        # before ingest_file() even gets called.
        processed_log["_file_mtimes"] = {}
        file_mtimes = processed_log["_file_mtimes"]

    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if args:
        files = [args[0]]
    else:
        files = (glob.glob(os.path.join(config.EXPORT_WATCH_DIR, "*.json")) +
                  glob.glob(os.path.join(config.EXPORT_WATCH_DIR, "*.zip")))
        if not files:
            # Unattended-safe: nothing new to do is a normal, quiet outcome
            # when this runs on a schedule -- not an error. NOTE: we do NOT
            # return here -- a prior bug did, which meant embed_new_photos()
            # (below) never ran on any day without a fresh export, silently
            # leaving photos already on disk un-embedded indefinitely.
            print(f"No .json or .zip files found in {config.EXPORT_WATCH_DIR}. "
                  f"Nothing to ingest this run.")
        else:
            # Newest export first, in case multiple old exports linger in the folder.
            files.sort(key=os.path.getmtime, reverse=True)

    total_new = 0
    # Phase 4: collect {photo_filename: entry_id} across every file processed
    # this run, then do a single embed_new_photos() pass at the end -- so a
    # run that touches multiple export files still only embeds photos once,
    # with the fullest possible mapping available.
    #
    # IMPORTANT: the mapping must be rebuilt from a file's entries even when
    # that file's mtime is unchanged (and text re-embedding is correctly
    # skipped below) -- otherwise photos whose entries were already marked
    # "processed" in a prior run never get their entry_id linked, since
    # ingest_file() (and its internal mapping loop) would never run for
    # them again. Rebuilding the map is just re-reading JSON already on
    # disk into a dict -- no re-embedding, no CLIP calls, effectively free.
    entry_photo_map = {}
    for f in files:
        current_mtime = os.path.getmtime(f)
        if not force and file_mtimes.get(f) == current_mtime:
            # Text is unchanged, so skip the expensive-ish re-embed path --
            # but still parse the file just far enough to refresh the photo
            # filename -> entry_id mapping, cheaply.
            try:
                json_path = resolve_to_json(f)
                with open(json_path, "r", encoding="utf-8") as jf:
                    raw_data = json.load(jf)
                for e in normalize_entries(raw_data):
                    if e.get("photo"):
                        entry_photo_map[e["photo"]] = str(e["id"])
            except Exception as ex:
                print(f"[{f}] Couldn't refresh photo mapping for unchanged "
                      f"file (non-fatal): {ex}")
            continue
        total_new += ingest_file(f, collection, processed_log, entry_photo_map)
        file_mtimes[f] = current_mtime

    save_processed_log(processed_log)
    print(f"\nDone. {total_new} entries newly embedded this run. "
          f"Collection now has {collection.count()} chunks total.")

    # Phase 4: embed any not-yet-embedded photos for visual search. Called
    # unconditionally, even if entry_photo_map ended up empty (e.g. every
    # export file's mtime was unchanged this run) -- embed_new_photos() scans
    # PHOTOS_DIR directly, so this still catches photos that were already on
    # disk from before Phase 4 existed. Safe/cheap to call every run: it
    # no-ops quickly if nothing new, and skips gracefully (Phases 1-3
    # unaffected) if CLIP/image_embed isn't available.
    embed_new_photos(entry_photo_map)


if __name__ == "__main__":
    main()