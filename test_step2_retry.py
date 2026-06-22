"""Standalone test for _fetch_page retry + error classification.

No pytest needed — run directly:
    .venv/bin/python test_step2_retry.py

Mocks the httpx client (no real API / no credits) and patches out the backoff
sleep, so it's instant and deterministic.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from lib import async_collect as c

_BAD_JSON = object()  # sentinel: response body that does NOT parse as JSON


def make_response(status, json_body=_BAD_JSON, text=""):
    r = MagicMock()
    r.status_code = status
    r.text = text
    if json_body is _BAD_JSON:
        r.json.side_effect = ValueError("not json")  # non-JSON body
    else:
        r.json.return_value = json_body
    return r


def make_client(get_side_effect):
    """An object whose async .get(url) yields the given responses (or raises)."""
    client = MagicMock()
    client.get = AsyncMock(side_effect=get_side_effect)
    return client


@patch("lib.async_collect.asyncio.sleep", new_callable=AsyncMock)
def run(_sleep):
    # 1. 429 then 200 → retry once, then succeed.
    client = make_client([make_response(429, text="rate limited"),
                          make_response(200, json_body=[{"signature": "abc"}])])
    result = asyncio.run(c._fetch_page(client, "http://x", "W01"))
    assert result == [{"signature": "abc"}], result
    assert client.get.await_count == 2, client.get.await_count
    print("PASS  429 → retry → 200 success")

    # 2. 404 + JSON 'Failed to find events' → NOT an error: return the dict so the
    #    caller treats a non-list as nothing-new. (The regression we just fixed.)
    body = {"error": "Failed to find events within the search period"}
    client = make_client([make_response(404, json_body=body)])
    result = asyncio.run(c._fetch_page(client, "http://x", "W02"))
    assert result == body, result
    assert client.get.await_count == 1, "404-nothing-new should not retry"
    print("PASS  404 + JSON 'no events' → nothing-new, returned (not raised)")

    # 3. 404 + non-JSON body (retired host, #16) → fail fast, NO retry.
    client = make_client([make_response(404, text="Not Found")])
    try:
        asyncio.run(c._fetch_page(client, "http://x", "W03"))
        assert False, "404 non-JSON should raise"
    except c.CollectionError as e:
        assert "dead endpoint" in str(e), str(e)
        assert client.get.await_count == 1, f"retried {client.get.await_count}×"
    print("PASS  404 + non-JSON → fail fast (dead endpoint)")

    # 4. 401 auth → fail fast regardless of body, NO retry.
    client = make_client([make_response(401, text="unauthorized")])
    try:
        asyncio.run(c._fetch_page(client, "http://x", "W04"))
        assert False, "401 should raise"
    except c.CollectionError as e:
        assert "not retryable" in str(e), str(e)
        assert client.get.await_count == 1
    print("PASS  401 auth → fail fast")

    # 5. network error every time → retry up to MAX_ATTEMPTS, then raise.
    client = make_client(httpx.ConnectError("boom"))
    try:
        asyncio.run(c._fetch_page(client, "http://x", "W05"))
        assert False, "exhausted retries should raise"
    except c.CollectionError as e:
        assert f"after {c.MAX_ATTEMPTS}" in str(e), str(e)
        assert client.get.await_count == c.MAX_ATTEMPTS, client.get.await_count
    print(f"PASS  network error → {c.MAX_ATTEMPTS} attempts → raise")

    # 6. 500, 500, then 200 → retry twice, then succeed.
    client = make_client([make_response(500, text="err"),
                          make_response(500, text="err"),
                          make_response(200, json_body=[])])
    result = asyncio.run(c._fetch_page(client, "http://x", "W06"))
    assert result == [], result
    print("PASS  500, 500 → retry → 200 success")

    print("\nALL TESTS PASSED ✅")


if __name__ == "__main__":
    run()
