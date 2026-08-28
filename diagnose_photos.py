"""
Quick diagnostic: check if photos are actually in the photos/ folder
and if server.py can find them.
"""

import os
import sys

# Add project dir to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
from ingest import PHOTOS_DIR

print("=" * 60)
print("PHOTOS FOLDER DIAGNOSTIC")
print("=" * 60)

print(f"\n📁 PHOTOS_DIR configured as: {PHOTOS_DIR}")
print(f"   (computed from config.VECTOR_DB_DIR: {config.VECTOR_DB_DIR})")

if not os.path.exists(PHOTOS_DIR):
    print(f"\n❌ PHOTOS_DIR does NOT exist yet. Creating it...")
    os.makedirs(PHOTOS_DIR, exist_ok=True)
    print(f"   Created: {PHOTOS_DIR}")

print(f"\n✅ PHOTOS_DIR exists: {PHOTOS_DIR}")

# List what's in there
photos = [f for f in os.listdir(PHOTOS_DIR) 
          if f.lower().endswith(('.jpg', '.jpeg', '.png', '.heic', '.gif', '.webp'))]

print(f"\n📸 Photos in folder: {len(photos)}")
if photos:
    print("\n   First 10 photos:")
    for f in sorted(photos)[:10]:
        full_path = os.path.join(PHOTOS_DIR, f)
        size_kb = os.path.getsize(full_path) / 1024
        print(f"     - {f} ({size_kb:.1f} KB)")
    if len(photos) > 10:
        print(f"     ... and {len(photos) - 10} more")
else:
    print("\n   ⚠️  No photos found! This means:")
    print("      1. ingest.py hasn't extracted them yet, OR")
    print("      2. Your Day One export doesn't include media")
    print("\n   To fix:")
    print("      - Re-export from Day One with 'Include Media' enabled")
    print("      - Run: python ingest.py path/to/new_export.zip")

# Check Chroma collection
print("\n" + "=" * 60)
print("CHROMA COLLECTION STATUS")
print("=" * 60)

try:
    import chromadb
    client = chromadb.PersistentClient(path=config.VECTOR_DB_DIR)
    
    text_collection = client.get_or_create_collection("journal")
    print(f"\n📚 Text collection: {text_collection.count()} chunks")
    
    try:
        import image_embed
        photo_collection = image_embed.get_photo_collection()
        print(f"📸 Photo collection: {photo_collection.count()} photos embedded")
        
        if photo_collection.count() > 0:
            # Sample a few to check metadata
            sample = photo_collection.get(limit=3, include=["metadatas"])
            print(f"\n   Sample photos in collection:")
            for photo_id, meta in zip(sample["ids"], sample["metadatas"]):
                entry_id = (meta or {}).get("entry_id", "")
                print(f"     - {photo_id}")
                print(f"       entry_id: {entry_id or '(blank!)'}")
        
    except ImportError:
        print(f"⚠️  image_embed not available (CLIP not installed)")
        
except Exception as e:
    print(f"❌ Error checking collections: {e}")

print("\n" + "=" * 60)
print("\n💡 Next steps:")
print("   1. If photos folder is empty: re-export from Day One with media")
print("   2. If photos are there: check that server.py can serve them")
print("   3. Try: python server.py and test http://localhost:5000/photos/<filename>")
