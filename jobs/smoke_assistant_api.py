from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request


def _request_json(url: str, *, method: str = "GET", headers: dict[str, str] | None = None, payload: dict | None = None) -> dict:
    data = None
    request_headers = dict(headers or {})
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/json")
    request = urllib.request.Request(url, data=data, headers=request_headers, method=method)
    with urllib.request.urlopen(request, timeout=30) as response:
        body = response.read().decode("utf-8")
    return json.loads(body) if body else {}


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke-check the assistant API and auth wiring.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--service-api-key", default="")
    parser.add_argument("--admin-api-key", default="")
    parser.add_argument("--user-id", default="smoke_user")
    parser.add_argument("--query", default="search for gaming earbuds")
    args = parser.parse_args()

    base = args.base_url.rstrip("/")
    try:
        health = _request_json(f"{base}/health")
        print("[smoke] /health:", health)

        if args.admin_api_key:
            details = _request_json(
                f"{base}/health/details",
                headers={"X-Admin-API-Key": args.admin_api_key},
            )
            print("[smoke] /health/details:", details)

        assistant_headers = {}
        if args.service_api_key:
            assistant_headers["X-API-Key"] = args.service_api_key

        assistant = _request_json(
            f"{base}/assistant",
            method="POST",
            headers=assistant_headers,
            payload={
                "query": args.query,
                "user_id": args.user_id,
                "top_k": 3,
                "include_tool_trace": True,
            },
        )
        print("[smoke] /assistant mode:", assistant.get("mode"))
        print("[smoke] /assistant answer:", assistant.get("answer"))
        print("[smoke] /assistant results:", len(list(assistant.get("results") or [])))

        conversation_id = str(assistant.get("conversation_id") or "").strip()
        if conversation_id:
            history_url = f"{base}/assistant/conversations/{urllib.parse.quote(conversation_id)}?user_id={urllib.parse.quote(args.user_id)}"
            history = _request_json(history_url, headers=assistant_headers)
            print("[smoke] /assistant/conversations count:", history.get("count"))

        return 0
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        print(f"[smoke] HTTP {exc.code}: {body}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"[smoke] failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
