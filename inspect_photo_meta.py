"""
One-off debug script: prints the raw "photos" field of Day One entries
that actually have a photo attached, so we can see the real shape of
that metadata (identifier/md5 field name, file extension field, etc.)
and compare it against what extract_photo_filename() in ingest.py
guesses.

Usage:
    python inspect_photo_meta.py path/to/your_export.json
"""

import sys
import json

if len(sys.argv) < 2:
    print("Usage: python inspect_photo_meta.py path\\to\\export.json")
    sys.exit(1)

with open(sys.argv[1], "r", encoding="utf-8") as f:
    data = json.load(f)

entries = data.get("entries", [])
print(f"Total entries: {len(entries)}")

shown = 0
for e in entries:
    photos = e.get("photos")
    if photos:
        print(f"\n--- entry uuid: {e.get('uuid')} ---")
        print(json.dumps(photos, indent=2))
        shown += 1
    if shown >= 3:
        break

if shown == 0:
    print("\nNo entries with a top-level 'photos' field found. "
          "Photos may be embedded in richText instead -- checking one entry's richText:")
    for e in entries:
        if e.get("richText") and "photo" in e.get("richText", "").lower():
            print(f"\n--- entry uuid: {e.get('uuid')} richText excerpt ---")
            print(e["richText"][:1500])
            break