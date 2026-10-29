"""Generate UI/docs copies from the authoritative backend ontology; --check is CI-safe."""

from pathlib import Path
import sys

root = Path(__file__).resolve().parents[1]
content = (root / "backend/app/dna/ontology.json").read_bytes()
for rel in ["frontend/src/dna-ontology.json", "docs/game_dna_rubric.yaml"]:
    p = root / rel
    if "--check" in sys.argv:
        if p.read_bytes() != content:
            raise SystemExit(f"Out of sync: {rel}")
    else:
        p.write_bytes(content)
print("Ontology copies consistent")
