import json
import pathlib

scratch = pathlib.Path(__file__).parent
chunk = json.loads((scratch / "chunk.json").read_text(encoding="utf-8"))
translation = (scratch / "translation.txt").read_text(encoding="utf-8").strip("\n")

glossary = {
    "the White Rabbit": "le Lapin Blanc",
    "the Duchess": "la Duchesse",
    "the Pool of Tears": "la Mare aux Larmes",
}

missing = [k for k, v in glossary.items() if v not in translation]
if missing:
    raise SystemExit(f"glossary renderings absent from translation: {missing}")

result = {
    "chunk_id": chunk["id"],
    "glossary": glossary,
    "translation": translation,
}
(scratch / "result.json").write_text(
    json.dumps(result, ensure_ascii=False), encoding="utf-8"
)
print(json.dumps({
    "chunk_id": result["chunk_id"],
    "glossary_terms": len(glossary),
    "translation_chars": len(translation),
    "source_chars": chunk["chars"],
    "paragraph_blocks": translation.count("\n\n") + 1,
}, ensure_ascii=False))
