from __future__ import annotations

import json
from pathlib import Path

from .builder import GraphSnapshot


class KnowledgeGraphExporter:
    def export_json(self, snapshot: GraphSnapshot, path: Path) -> None:
        payload = {
            "generated_at": snapshot.generated_at.isoformat(),
            "nodes": [node.__dict__ for node in snapshot.nodes],
            "edges": [edge.__dict__ for edge in snapshot.edges],
        }
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
