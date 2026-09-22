"""Read authenticated service status and recipes without printing credentials."""

from __future__ import annotations

import json
from urllib.request import HTTPRedirectHandler, Request, build_opener

from suoku.config import load_config


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        return None


def main() -> None:
    config = load_config()
    token = config.get("SUOKU_API_TOKEN", "")
    if not token:
        raise SystemExit("Run suoku init or set SUOKU_API_TOKEN first.")
    base = config.get("SUOKU_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
    opener = build_opener(NoRedirect())
    for route in ("/v1/status", "/v1/insight-recipes"):
        request = Request(base + route, headers={"Authorization": f"Bearer {token}"})
        with opener.open(request, timeout=30) as response:
            print(json.dumps({"route": route, "response": json.load(response)}, indent=2))


if __name__ == "__main__":
    main()
