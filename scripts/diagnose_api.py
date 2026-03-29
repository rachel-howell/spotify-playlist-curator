#!/usr/bin/env python3
"""Diagnostic script to verify Spotify API encoding fixes and known regressions.

Run: .venv/bin/python scripts/diagnose_api.py

Tests:
  A — Batch artists: inline IDs vs params dict (URL encoding bug)
  B — Playlist fields: inline vs params dict (URL encoding bug)
  C — Search limit boundary (Spotify API cap)
  D — Playlist items regression (Spotify-side)
  E — Queue URI encoding check (latent bug)
"""

import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

import requests
from spotify_client import SpotifyClient, API_BASE


def header(label: str) -> None:
    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"{'='*60}")


def test_a_batch_artists(client: SpotifyClient) -> None:
    """Test A: Batch artists — inline IDs vs params dict."""
    header("Test A: Batch artists URL encoding")

    # Get 3 artist IDs from user's top artists
    top = client.get_top_artists(limit=3)
    if len(top) < 3:
        print("SKIP: Need at least 3 top artists")
        return
    ids = [a["id"] for a in top]
    names = [a["name"] for a in top]
    print(f"  Using artists: {', '.join(names)}")
    id_str = ",".join(ids)

    # Method 1: IDs inline in URL (correct — what our fix does)
    try:
        resp = client._api_request("GET", f"artists?ids={id_str}")
        artists = resp.json().get("artists", [])
        print(f"  Inline URL:  {resp.status_code} — got {len(artists)} artists ✓")
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response is not None else "???"
        url = e.response.request.url if e.response is not None else "unknown"
        body = e.response.text[:200] if e.response is not None else ""
        print(f"  Inline URL:  {status} ✗")
        print(f"    URL sent:  {url}")
        print(f"    Response:  {body}")
    except Exception as e:
        print(f"  Inline URL:  error — {type(e).__name__}: {e}")

    # Method 1b: Raw requests.get with inline URL (bypass _api_request)
    try:
        client._ensure_fresh_token()
        headers = {
            "Authorization": f"Bearer {client._sp._auth}",
            "Content-Type": "application/json",
        }
        raw_url = f"{API_BASE}/artists?ids={id_str}"
        resp_raw = requests.get(raw_url, headers=headers)
        print(f"  Raw inline:  {resp_raw.status_code} — {'✓' if resp_raw.ok else '✗'}")
        print(f"    URL sent:  {resp_raw.request.url}")
        if not resp_raw.ok:
            print(f"    Response:  {resp_raw.text[:200]}")
        else:
            artists = resp_raw.json().get("artists", [])
            print(f"    Got {len(artists)} artists")
    except Exception as e:
        print(f"  Raw inline:  error — {e}")

    # Method 2: IDs in params dict (old buggy way — commas get encoded)
    try:
        client._ensure_fresh_token()
        headers = {
            "Authorization": f"Bearer {client._sp._auth}",
            "Content-Type": "application/json",
        }
        resp2 = requests.get(
            f"{API_BASE}/artists",
            headers=headers,
            params={"ids": id_str},
        )
        print(f"  Params dict: {resp2.status_code} — {'✓' if resp2.ok else '✗ (expected — commas encoded as %2C)'}")
        if not resp2.ok:
            print(f"    URL sent: {resp2.request.url}")
    except Exception as e:
        print(f"  Params dict: error — {e}")


def test_b_playlist_fields(client: SpotifyClient) -> None:
    """Test B: Playlist fields — inline vs params dict."""
    header("Test B: Playlist fields URL encoding")

    # Find a playlist from the user's account
    playlists = client.list_playlists(limit=1)
    if not playlists:
        print("  SKIP: No playlists found")
        return
    playlist_id = playlists[0]["id"]
    playlist_name = playlists[0].get("name", "unknown")
    print(f"  Using playlist: \"{playlist_name}\" ({playlist_id})")

    fields = "id,name,tracks.total,public,owner.display_name,collaborative"

    # Method 1: Fields inline (correct — what our fix does)
    try:
        resp = client._api_request("GET", f"playlists/{playlist_id}?fields={fields}")
        data = resp.json()
        total = data.get("tracks", {}).get("total")
        url = resp.request.url
        print(f"  Inline URL:  {resp.status_code} — tracks.total={total} {'✓' if total is not None else '✗'}")
        print(f"    URL sent:  {url}")
        if total is None:
            import json as _json
            print(f"    Response:  {_json.dumps(data, indent=2)[:300]}")
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response is not None else "???"
        url = e.response.request.url if e.response is not None else "unknown"
        print(f"  Inline URL:  {status} ✗")
        print(f"    URL sent:  {url}")
    except Exception as e:
        print(f"  Inline URL:  error — {type(e).__name__}: {e}")

    # Method 2: Fields in params dict (old buggy way)
    try:
        client._ensure_fresh_token()
        headers = {
            "Authorization": f"Bearer {client._sp._auth}",
            "Content-Type": "application/json",
        }
        resp2 = requests.get(
            f"{API_BASE}/playlists/{playlist_id}",
            headers=headers,
            params={"fields": fields},
        )
        print(f"    URL sent:  {resp2.request.url}")
        data2 = resp2.json() if resp2.ok else {}
        total2 = data2.get("tracks", {}).get("total")
        if resp2.ok and total2 is not None:
            print(f"  Params dict: {resp2.status_code} — tracks.total={total2} (params also worked here)")
        elif resp2.ok:
            print(f"  Params dict: {resp2.status_code} — tracks.total=MISSING ✗ (fields silently ignored)")
        else:
            print(f"  Params dict: {resp2.status_code} ✗")
            print(f"    Response:  {resp2.text[:200]}")
    except Exception as e:
        print(f"  Params dict: error — {e}")

    # Method 3: Fixed get_playlist_info (uses items.total + normalization)
    try:
        info = client.get_playlist_info(playlist_id)
        total3 = info.get("tracks", {}).get("total")
        print(f"  Fixed func:  tracks.total={total3} {'✓' if total3 is not None else '✗'}")
    except Exception as e:
        print(f"  Fixed func:  error — {e}")


def test_c_search_limit(client: SpotifyClient) -> None:
    """Test C: Search limit boundary."""
    header("Test C: Search limit boundary")

    sp = client._with_client()
    for limit in [10, 11, 12, 15, 20]:
        try:
            result = sp.search(q="Mitski", type="track", limit=limit)
            count = len(result.get("tracks", {}).get("items", []))
            print(f"  limit={limit:2d}: OK — got {count} results ✓")
        except Exception as e:
            msg = str(e)
            if "Invalid limit" in msg or "400" in msg:
                print(f"  limit={limit:2d}: 400 Invalid limit ✗")
            else:
                print(f"  limit={limit:2d}: error — {msg}")


def test_d_playlist_items(client: SpotifyClient) -> None:
    """Test D: Playlist items regression (Spotify-side)."""
    header("Test D: Playlist items (known Spotify regression)")

    playlist_id = "1MkKWo8Ggs8TpD6dJi1JSp"  # "the witches are angry"

    # Direct /items endpoint
    try:
        resp = client._api_request(
            "GET", f"playlists/{playlist_id}/items",
            params={"limit": 50, "offset": 0, "additional_types": "track"},
        )
        items = resp.json().get("items", [])
        print(f"  /items endpoint:        {resp.status_code} — {len(items)} items")
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response else "???"
        print(f"  /items endpoint:        {status} ✗ (expected — Spotify regression)")

    # Spotipy playlist_items
    try:
        sp = client._with_client()
        result = sp.playlist_items(playlist_id, limit=50)
        items = result.get("items", [])
        print(f"  spotipy playlist_items: 200 — {len(items)} items")
    except Exception as e:
        print(f"  spotipy playlist_items: error — {e}")

    # With market=US
    try:
        resp = client._api_request(
            "GET", f"playlists/{playlist_id}/items",
            params={"limit": 50, "offset": 0, "additional_types": "track", "market": "US"},
        )
        items = resp.json().get("items", [])
        print(f"  /items + market=US:     {resp.status_code} — {len(items)} items")
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response else "???"
        print(f"  /items + market=US:     {status} ✗")

    # Fallback via full playlist object
    try:
        sp = client._with_client()
        data = sp.playlist(playlist_id, additional_types=("track",))
        items = (data.get("tracks") or {}).get("items", [])
        print(f"  GET /playlists/{{id}}:    200 — {len(items)} embedded items")
    except Exception as e:
        print(f"  GET /playlists/{{id}}:    error — {e}")


def test_e_queue_uri_encoding(client: SpotifyClient) -> None:
    """Test E: Check if queue_track would encode colons in spotify:track:... URIs."""
    header("Test E: Queue URI encoding (dry-run)")

    uri = "spotify:track:4uLU6hMCjMI75M1A2tKUQC"  # example track

    # Build the URL as our fixed code does (inline)
    inline_url = f"{API_BASE}/me/player/queue?uri={uri}"
    print(f"  Fixed (inline):  {inline_url}")
    has_encoded = "%3A" in inline_url
    print(f"    Contains %3A:  {'YES ✗' if has_encoded else 'no ✓'}")

    # Build the URL as the old code did (params dict)
    req = requests.Request("POST", f"{API_BASE}/me/player/queue", params={"uri": uri})
    prepared = req.prepare()
    print(f"  Old (params):    {prepared.url}")
    has_encoded_old = "%3A" in (prepared.url or "")
    print(f"    Contains %3A:  {'YES ✗ (would break)' if has_encoded_old else 'no ✓'}")


def main() -> None:
    print("Spotify API Diagnostic")
    print("=" * 60)

    try:
        client = SpotifyClient.from_env()
    except RuntimeError as e:
        print(f"Setup error: {e}")
        sys.exit(1)

    test_a_batch_artists(client)
    test_b_playlist_fields(client)
    test_c_search_limit(client)
    test_d_playlist_items(client)
    test_e_queue_uri_encoding(client)

    print(f"\n{'='*60}")
    print("  Done. Review results above.")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
