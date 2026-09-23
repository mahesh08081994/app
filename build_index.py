"""Run once, and re-run whenever knowledge/ changes:

    python build_index.py
"""
from __future__ import annotations
import pathlib
import chromadb
from app.config import CHROMA_DIR

client = chromadb.PersistentClient(path=CHROMA_DIR)
collection = client.get_or_create_collection("knowledge")

docs, ids = [], []
files = list(pathlib.Path("knowledge").glob("*.txt"))

for i, path in enumerate(files):
    text = path.read_text()
    # naive chunking -- split into ~500 character pieces
    for j in range(0, len(text), 500):
        docs.append(text[j:j + 500])
        ids.append(f"{path.stem}-{j}")

if docs:
    collection.upsert(documents=docs, ids=ids)

print(f"Indexed {len(docs)} chunks from {len(files)} files.")
