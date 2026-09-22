"""Regenerate the SDK contract from the actual API factory; no model required."""

import json
import tempfile
from pathlib import Path

from suoku.server.app import Settings, create_app

root = Path(__file__).resolve().parents[1]
# Generate from FastAPI's route declarations so the Python API and TypeScript client
# cannot drift silently.
with tempfile.TemporaryDirectory() as directory:
    app = create_app(Settings(Path(directory), "schema-generation-placeholder-token-0000"))
    target = root / "schema" / "openapi.json"
    target.parent.mkdir(exist_ok=True)
    target.write_text(json.dumps(app.openapi(), indent=2) + "\n")
