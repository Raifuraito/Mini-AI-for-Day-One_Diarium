"""
backfill_photo_metadata.py

Scans your already-processed journal entries and patches the entry_id 
metadata for any photos that are orphaned (have blank/missing entry_id).

This is a one-time fix for photo embeddings created before entry tracking 
was implemented. After this runs, all future photos will have correct 
entry_id values automatically.

Usage:
    python backfill_photo_metadata.py path/to/export.json
    # or, to process all exports that have ever been ingested:
    python backfill_photo_metadata.py --all-in-folder path/to/folder
"""

import json
import os
import sys
import os as _os
sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import glob

import chromadb
import config
from ingest import normalize_entries


def backfill_from_file(json_path, collection):
    """
    Reads a single journal JSON export, builds photo->entry_id mapping,
    then patches any orphaned photos in Chroma with their entry_id.
    Returns (patched_count, photo_count_found).
    """
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            raw_data = json.load(f)
    except Exception as e:
        print(f"  ❌ Couldn't read {json_path}: {e}")
        return 0, 0

    entries = normalize_entries(raw_data)
    
    # Build photo -> entry_id map from this file
    photo_map = {}
    for e in entries:
        if e.get("photo"):
            photo_map[e["photo"]] = str(e["id"])
    
    if not photo_map:
        print(f"  ℹ️  No photos found in {os.path.basename(json_path)}")
        return 0, 0
    
    # Get all photo entries from Chroma that are in our map
    existing_ids = collection.get(include=[])["ids"]
    backfillable = [photo_id for photo_id in existing_ids if photo_id in photo_map]
    
    if not backfillable:
        print(f"  ℹ️  {len(photo_map)} photos in this export aren't embedded yet (will be on next ingest.py run)")
        return 0, len(photo_map)
    
    # Fetch their current metadata
    existing_meta = collection.get(ids=backfillable, include=["metadatas"])
    
    # Find which ones actually need patching (currently have blank entry_id)
    to_update_ids, to_update_metas = [], []
    for photo_id, meta in zip(existing_meta["ids"], existing_meta["metadatas"]):
        current_entry_id = (meta or {}).get("entry_id") or ""
        new_entry_id = photo_map.get(photo_id, "")
        if not current_entry_id and new_entry_id:
            to_update_ids.append(photo_id)
            to_update_metas.append({"entry_id": new_entry_id})
    
    if to_update_ids:
        collection.update(ids=to_update_ids, metadatas=to_update_metas)
        print(f"  ✅ Patched {len(to_update_ids)} orphaned photo(s) with their entry_id")
    else:
        print(f"  ℹ️  All {len(backfillable)} photos in this export already have entry_id set")
    
    return len(to_update_ids), len(photo_map)


def main():
    client = chromadb.PersistentClient(path=config.VECTOR_DB_DIR)
    photo_collection = None
    
    # Try to import image_embed to get the photo collection
    try:
        import image_embed
        photo_collection = image_embed.get_photo_collection()
    except ImportError:
        print("❌ image_embed not found (CLIP/open_clip not installed)")
        print("   Run: pip install open-clip-torch pillow")
        return
    
    if photo_collection.count() == 0:
        print("⚠️  No photos embedded yet -- nothing to backfill.")
        return
    
    print(f"Found {photo_collection.count()} photos in the database.\n")
    
    if "--all-in-folder" in sys.argv:
        if len(sys.argv) < 3:
            print('Usage: python backfill_photo_metadata.py --all-in-folder /path/to/folder')
            return
        folder = sys.argv[2]
        json_files = glob.glob(os.path.join(folder, "*.json"))
        if not json_files:
            print(f"❌ No .json files found in {folder}")
            return
        print(f"Processing {len(json_files)} export files...\n")
        
        total_patched = 0
        total_photos_seen = 0
        for json_file in sorted(json_files):
            print(f"📄 {os.path.basename(json_file)}")
            patched, photos_found = backfill_from_file(json_file, photo_collection)
            total_patched += patched
            total_photos_seen += photos_found
        
        print(f"\n✅ Done. Patched {total_patched} photos total.")
    
    elif len(sys.argv) > 1:
        json_path = sys.argv[1]
        if not os.path.exists(json_path):
            print(f"❌ File not found: {json_path}")
            return
        print(f"Processing: {json_path}\n")
        patched, photos_found = backfill_from_file(json_path, photo_collection)
        print(f"\n✅ Done. Patched {patched} photos.")
    
    else:
        print("Usage:")
        print("  python backfill_photo_metadata.py path/to/export.json")
        print("  python backfill_photo_metadata.py --all-in-folder path/to/folder")
        print("\nThis scans journal exports and links orphaned photos to their entries.")


if __name__ == "__main__":
    main()