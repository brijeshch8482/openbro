"""Specialist routing: tree of category-specific LoRA adapters layered
on top of a single base model in VRAM. See docs/SPECIALISTS.md.
"""

from __future__ import annotations

SPECIALIST_VERSION = "0.1.0"

# Where the categories db, embeddings, and per-category adapters live.
SPECIALIST_ROOT = "D:/OpenBro-teting/specialists"
CATEGORIES_DB = "D:/OpenBro-teting/specialists/categories.db"
ADAPTERS_DIR = "D:/OpenBro-teting/specialists/adapters"
EMBEDDINGS_FILE = "D:/OpenBro-teting/specialists/category_embeddings.npy"
