import json

with open("translation.txt", encoding="utf-8") as f:
    translation = f.read().rstrip("\n")

with open("chunk.json", encoding="utf-8") as f:
    chunk = json.load(f)

glossary = {
    "the White Rabbit": "le Lapin Blanc",
    "the Knave of Hearts": "le Valet de Cœur",
    "the Queen of Hearts": "la Reine de Cœur",
    "the March Hare": "le Lièvre de Mars",
    "the Duchess": "la Duchesse",
    "the Gryphon": "le Griffon",
    "the Mock Turtle": "la Fausse Tortue",
    "Wonderland": "le Pays des Merveilles",
}

missing = [name for name, fr in glossary.items() if fr not in translation]
if missing:
    raise SystemExit(f"glossary values missing from translation: {missing}")

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
}, ensure_ascii=False))
