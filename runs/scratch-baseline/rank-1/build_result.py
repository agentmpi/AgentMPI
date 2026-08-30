import json

with open("translation.txt", encoding="utf-8") as f:
    translation = f.read().rstrip("\n")

with open("chunk.json", encoding="utf-8") as f:
    chunk = json.load(f)

glossary = {
    "the White Rabbit": "le Lapin Blanc",
    "the Rabbit-Hole": "le Terrier du Lapin",
}

missing = [v for v in glossary.values() if v not in translation]
if missing:
    raise SystemExit("glossary values missing from translation: %r" % missing)

result = {
    "chunk_id": chunk["id"],
    "glossary": glossary,
    "translation": translation,
}

with open("result.json", "w", encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False)

print(json.dumps({
    "chunk_id": result["chunk_id"],
    "glossary_terms": len(glossary),
    "translation_chars": len(translation),
    "source_chars": chunk["chars"],
    "paragraphs_src": chunk["text"].count("\n\n"),
    "paragraphs_tr": translation.count("\n\n"),
}, ensure_ascii=False))
