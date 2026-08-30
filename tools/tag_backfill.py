"""
tag_backfill.py

Fills in tags (people/places/themes) for journal entries that don't have
them yet -- either because they were embedded before tagging existed, or
because a previous run of this script was interrupted or limited on
purpose.

Safe to run repeatedly and safe to interrupt (Ctrl+C) at any point: every
batch is written to the database as soon as it's done, so nothing is lost
if you stop partway through -- the next run just picks up where you left
off. An entry Claude looks at but finds nothing notable in (no people,
places, or themes worth tagging) is remembered as "already tried" so it's
never re-sent and re-paid-for on a future run.

Usage:
    python tag_backfill.py --count       Just report how many entries still
                                          need tags. Free, makes no API calls.
    python tag_backfill.py               Tag up to 300 entries (the default
                                          limit for one run), then stop.
    python tag_backfill.py --limit 500   Tag up to 500 entries this run.
    python tag_backfill.py --all         Tag everything that still needs it,
                                          no limit -- use this for a one-time
                                          full catch-up.

Cost: with the default TAG_EXTRACTION_MODEL (Haiku), this typically comes
out to a small fraction of a cent per entry. Each run prints a rough
estimate of what it spent, based on character counts rather than the
API's actual token usage (extract_tags_batch() doesn't report that) --
treat it as a ballpark, not an exact invoice.
"""

import sys
import os as _os
sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import json
import os

import chromadb

import config
from ingest import get_tagging_client, extract_tags_batch


# Rough per-token prices (dollars per token, not per million) for the cost
# estimate below. Update these if TAG_EXTRACTION_MODEL ever points at a
# different model with different pricing -- checked against
# platform.claude.com/docs/en/about-claude/pricing as of Aug 2026.
_PRICE_PER_TOKEN = {
    "claude-haiku-4-5": {"input": 1.00 / 1_000_000, "output": 5.00 / 1_000_000},
    "claude-sonnet-4-6": {"input": 3.00 / 1_000_000, "output": 15.00 / 1_000_000},
}
_DEFAULT_LIMIT = 300


def _log_path():
    return getattr(config, "TAG_BACKFILL_LOG", "./tag_backfill_log.json")


def load_attempted_log():
    path = _log_path()
    if os.path.exists(path):
        with open(path, "r") as f:
            return json.load(f)
    return {}


def save_attempted_log(log):
    with open(_log_path(), "w") as f:
        json.dump(log, f, indent=2)


def find_entries_needing_tags(ids, docs, metas, attempted_log):
    """
    Returns a list of (entry_id, first_chunk_text) for every entry that
    has no tags yet and hasn't already been attempted before.

    Every chunk of one entry shares the same tags string (see ingest.py's
    ingest_file(), which writes them all in one pass), so checking any one
    chunk tells you about the whole entry. Only the entry's first chunk
    (id ending "_0") is returned as the text source -- extract_tags_batch()
    truncates to TAG_SNIPPET_CHARS (800 chars by default) anyway, and a
    first chunk is at least CHUNK_SIZE_CHARS (1500) long, so this is
    exactly as much text as a normal ingest.py tagging call would use.
    """
    entry_has_tags = {}
    entry_first_chunk = {}

    for chunk_id, doc, meta in zip(ids, docs, metas):
        meta = meta or {}
        entry_id = meta.get("entry_id")
        if not entry_id:
            continue
        if meta.get("tags"):
            entry_has_tags[entry_id] = True
        else:
            entry_has_tags.setdefault(entry_id, False)
        if chunk_id.endswith("_0"):
            entry_first_chunk[entry_id] = doc

    needing = []
    for entry_id, has_tags in entry_has_tags.items():
        if has_tags or entry_id in attempted_log:
            continue
        text = entry_first_chunk.get(entry_id)
        if not text:
            continue  # shouldn't happen, but skip rather than crash
        needing.append((entry_id, text))

    return needing


def apply_tags(collection, entry_id_to_tags, all_metas_by_chunk):
    """
    Writes new tags into every chunk belonging to each newly-tagged entry.
    Always writes back the chunk's FULL existing metadata (date, photo,
    sentiment, entry_id) with just "tags" changed, rather than a
    tags-only dict -- so nothing else gets wiped out, regardless of
    whether Chroma's update() replaces or merges metadata under the hood.
    """
    update_ids, update_metas = [], []
    for chunk_id, meta in all_metas_by_chunk.items():
        entry_id = (meta or {}).get("entry_id")
        if entry_id in entry_id_to_tags:
            new_meta = dict(meta)
            new_meta["tags"] = entry_id_to_tags[entry_id]
            update_ids.append(chunk_id)
            update_metas.append(new_meta)
    if update_ids:
        collection.update(ids=update_ids, metadatas=update_metas)
    return len(update_ids)


def estimate_batch_cost(pairs, model):
    """
    Rough cost estimate (character-count based, ~4 chars/token) for one
    tagging batch. Not exact -- extract_tags_batch() doesn't return real
    token usage, and estimating here avoids changing that shared
    function's return type, since ingest.py depends on it too.
    """
    snippet_chars = getattr(config, "TAG_SNIPPET_CHARS", 800)
    prompt_chars = sum(len(text[:snippet_chars]) for _, text in pairs) + 300
    input_tokens = prompt_chars / 4
    output_tokens = len(pairs) * 20  # short tag lists, roughly
    price = _PRICE_PER_TOKEN.get(model, {"input": 0.0, "output": 0.0})
    return input_tokens * price["input"] + output_tokens * price["output"]


def main():
    count_only = "--count" in sys.argv
    do_all = "--all" in sys.argv
    limit = _DEFAULT_LIMIT
    if "--limit" in sys.argv:
        try:
            limit = int(sys.argv[sys.argv.index("--limit") + 1])
        except (IndexError, ValueError):
            print("--limit needs a number after it, e.g. --limit 500")
            return

    client = chromadb.PersistentClient(path=config.VECTOR_DB_DIR)
    collection = client.get_or_create_collection("journal")
    if collection.count() == 0:
        print("Journal collection is empty -- run ingest.py first.")
        return

    all_data = collection.get(include=["documents", "metadatas"])
    ids, docs, metas = all_data["ids"], all_data["documents"], all_data["metadatas"]
    all_metas_by_chunk = dict(zip(ids, metas))

    attempted_log = load_attempted_log()
    needing = find_entries_needing_tags(ids, docs, metas, attempted_log)

    print(f"{len(needing)} entries have never been tagged.")
    if count_only:
        return
    if not needing:
        print("Nothing to do.")
        return

    batch_entries = needing if do_all else needing[:limit]
    if not do_all and len(needing) > limit:
        print(f"Tagging {limit} of them this run (use --limit N or --all to "
              f"do more/all in one go). Re-run this script anytime to "
              f"continue with the rest.")
    else:
        print(f"Tagging all {len(batch_entries)} of them.")

    tagging_client = get_tagging_client()
    batch_size = getattr(config, "TAG_BATCH_SIZE", 15)

    total_tagged = 0
    total_cost = 0.0
    for i in range(0, len(batch_entries), batch_size):
        pairs = batch_entries[i:i + batch_size]

        tags_result = extract_tags_batch(pairs, tagging_client)
        apply_tags(collection, tags_result, all_metas_by_chunk)
        total_cost += estimate_batch_cost(pairs, config.TAG_EXTRACTION_MODEL)

        # Every entry in this batch counts as "attempted" now, found or
        # not -- this is what stops an entry with genuinely nothing
        # notable in it from being re-sent (and re-paid-for) every run.
        for entry_id, _ in pairs:
            attempted_log[entry_id] = True
        save_attempted_log(attempted_log)

        total_tagged += len(tags_result)
        done = min(i + batch_size, len(batch_entries))
        print(f"  -> batch {i // batch_size + 1}: {len(tags_result)}/{len(pairs)} "
              f"entries got tags ({done}/{len(batch_entries)} processed)")

    remaining = len(needing) - len(batch_entries)
    print(f"\nDone this run: {total_tagged} entries newly tagged "
          f"(out of {len(batch_entries)} attempted).")
    print(f"Rough cost this run: ${total_cost:.4f} (estimate, not exact).")
    if remaining > 0:
        print(f"{remaining} entries still untagged -- run this script "
              f"again anytime to keep going.")
    else:
        print("All caught up -- every entry has been tagged (or Claude "
              "found nothing notable to tag in it).")


if __name__ == "__main__":
    main()
