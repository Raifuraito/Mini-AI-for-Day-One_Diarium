"""
Image embeddings for visual search over journal photos (Phase 4).

Uses open_clip (a free, local implementation of OpenAI's CLIP model) to
turn photos into vectors that can be compared against a text query's
vector -- this is what makes "find the photo where I'm at the beach"
possible even when the entry's text never says "beach".

Kept as its own module, separate from the text-embedding pipeline in
ingest.py/ask.py, so importing it is optional: if the CLIP model can't
load (no internet on first run, out of disk space, etc.), Phases 1-3
keep working exactly as before -- only Phase 4's visual search degrades.
"""

import os
import chromadb

import config

# Model choice: ViT-B-32 is CLIP's smallest/fastest common variant --
# good enough for personal-journal-scale photo search, without the much
# larger download/RAM footprint of bigger CLIP variants. Swap to
# "ViT-L-14" (openai pretrained) for noticeably better accuracy at the
# cost of a larger download and slower embedding, if this ever matters.
CLIP_MODEL_NAME = "ViT-B-32"
CLIP_PRETRAINED = "openai"

PHOTO_COLLECTION_NAME = "journal_photos"

_model = None
_preprocess = None
_tokenizer = None


def _load_clip():
    """Lazy-loads CLIP on first use, not at import time -- so importing
    this module doesn't force a model download just to check availability."""
    global _model, _preprocess, _tokenizer
    if _model is not None:
        return
    import open_clip
    import torch
    _model, _, _preprocess = open_clip.create_model_and_transforms(
        CLIP_MODEL_NAME, pretrained=CLIP_PRETRAINED
    )
    _tokenizer = open_clip.get_tokenizer(CLIP_MODEL_NAME)
    _model.eval()


def is_available():
    """Checks whether CLIP can actually be loaded, without raising.
    Use this to decide whether to offer visual search at all."""
    try:
        _load_clip()
        return True
    except Exception as e:
        print(f"Image embeddings unavailable: {e}")
        return False


def embed_image(image_path):
    """Returns a CLIP embedding (list of floats) for a single image file."""
    _load_clip()
    import torch
    from PIL import Image

    image = Image.open(image_path).convert("RGB")
    image_input = _preprocess(image).unsqueeze(0)
    with torch.no_grad():
        features = _model.encode_image(image_input)
        features /= features.norm(dim=-1, keepdim=True)  # normalize for cosine similarity
    return features[0].tolist()


def embed_text_query(text):
    """
    Returns a CLIP embedding for a text query, in the SAME vector space
    as embed_image() -- this cross-modal alignment is what lets a text
    question like "beach photo" match against image embeddings directly.
    """
    _load_clip()
    import torch

    tokens = _tokenizer([text])
    with torch.no_grad():
        features = _model.encode_text(tokens)
        features /= features.norm(dim=-1, keepdim=True)
    return features[0].tolist()


def get_photo_collection():
    client = chromadb.PersistentClient(path=config.VECTOR_DB_DIR)
    # embedding_function=None: we supply our own CLIP embeddings directly
    # (via embeddings=... on add/query) rather than letting Chroma compute
    # its own text-based embeddings for this collection.
    return client.get_or_create_collection(
        PHOTO_COLLECTION_NAME, embedding_function=None
    )
