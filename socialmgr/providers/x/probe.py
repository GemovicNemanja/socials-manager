"""Day-1 probes (IMPLEMENTATION_PLAN.md §5): facts about the X API we must observe, not assume.

(a) the owner timeline's rate-limit headers;
(b) whether the follower list is newest first. X returns no follow timestamps, so a single call
    can't show the order. Each probe saves the newest ``follower_sample`` follower ids; the next
    probe (ideally a day later, after gaining a follower) compares. Under newest-first ordering,
    the new list is [followers gained since] + [the previous list minus unfollowers], so every id
    not seen before must come before every id seen before, and the previously seen ids keep their
    relative order. Ids after the last previously seen id are ignored (they may simply be older
    followers beyond the previous sample). Verdict: ``newest_first`` when at least one new
    follower sits on top and nothing contradicts, ``not_newest_first`` on any contradiction,
    ``undetermined`` otherwise;
(c) whether ``GET /2/tweets/analytics`` answers for a post at least 7 days old, and its shape.

The report never contains tokens. It is printed and written to ``data_dir/probes/``.
"""

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy.orm import Session, sessionmaker

from socialmgr.core.config import Settings
from socialmgr.providers.x.api import RateLimit, XApi
from socialmgr.providers.x.errors import ReauthRequired
from socialmgr.providers.x.provider import OWNED_READ_USD
from socialmgr.providers.x.tokens import find_credential, fresh_access_token

PROBE_DIR = "probes"
FOLLOWER_SAMPLE = 200
ANALYTICS_MIN_AGE = timedelta(days=7)
ANALYTICS_FIELDS = (
    "impressions,engagements,likes,retweets,replies,quote_tweets,bookmarks,user_profile_clicks,"
    "url_clicks,follows,timestamp,timestamped_metrics"
)
KEPT_HEADERS = ("x-rate-limit-limit", "x-rate-limit-remaining", "x-rate-limit-reset", "x-access-level")


def _iso(t: datetime) -> str:
    return t.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _headers(response: httpx.Response) -> dict[str, str]:
    return {h: response.headers[h] for h in KEPT_HEADERS if h in response.headers}


def _json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return {"text": response.text[:2000]}


def shape(value: Any, depth: int = 0) -> Any:
    """A type skeleton of a JSON value: what the response looks like, without the data."""
    if depth > 6:
        return "..."
    if isinstance(value, dict):
        return {k: shape(v, depth + 1) for k, v in value.items()}
    if isinstance(value, list):
        return [shape(value[0], depth + 1), f"... {len(value)} item(s)"] if value else []
    return type(value).__name__


def _truncate(value: Any, items: int = 3) -> Any:
    if isinstance(value, dict):
        return {k: _truncate(v, items) for k, v in value.items()}
    if isinstance(value, list):
        return [_truncate(v, items) for v in value[:items]]
    return value


def follower_order_verdict(previous: list[str] | None, current: list[str]) -> dict[str, Any]:
    if not previous:
        return {
            "verdict": "undetermined",
            "reason": "No earlier probe to compare with. "
            "Run `socialmgr probe` again after gaining a follower.",
        }
    seen = set(previous)
    old_positions = [i for i, f in enumerate(current) if f in seen]
    if not old_positions:
        return {"verdict": "undetermined", "reason": "No overlap with the previous sample."}
    first_old, last_old = old_positions[0], old_positions[-1]
    new_on_top = first_old
    interleaved = [current[i] for i in range(first_old, last_old) if current[i] not in seen]
    old_now = [f for f in current if f in seen]
    old_then = [f for f in previous if f in set(old_now)]
    reordered = old_now != old_then
    counts = {"new_on_top": new_on_top, "new_between_known": len(interleaved), "known_reordered": reordered}
    if interleaved or reordered:
        return {"verdict": "not_newest_first", "reason": "New followers appear among known ones.", **counts}
    if new_on_top == 0:
        return {
            "verdict": "undetermined",
            "reason": "No new followers since the previous probe; the order of known followers is stable.",
            **counts,
        }
    return {"verdict": "newest_first", "reason": "Every new follower is above every known one.", **counts}


def _previous_follower_sample(probe_dir: Path) -> tuple[str | None, list[str] | None]:
    for path in sorted(probe_dir.glob("probe-*.json"), reverse=True):
        try:
            ids = json.loads(path.read_text())["follower_order"]["sample_ids"]
        except (KeyError, TypeError, ValueError):
            continue
        if ids:
            return path.name, [str(i) for i in ids]
    return None, None


def run_probe(
    settings: Settings,
    factory: sessionmaker[Session],
    *,
    http: httpx.Client,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    follower_sample: int = FOLLOWER_SAMPLE,
) -> tuple[dict[str, Any], Path]:
    from socialmgr.services.pipeline import owner_account

    now = clock()
    with factory() as session:
        owner = owner_account(session, settings.workspace_id)
        cred = find_credential(session, owner.id) if owner else None
        if owner is None or cred is None:
            raise ReauthRequired("No X account is connected.")
        owner_id = owner.id
        refreshed_before = cred.refreshed_at

    token = fresh_access_token(settings, factory, http, owner_id, now)
    api = XApi(http, settings.x.api_base, token)
    me = api.get("/2/users/me").body["data"]
    user_id = str(me["id"])
    resources = 1
    report: dict[str, Any] = {
        "probed_at": now.isoformat(),
        "account": {"id": user_id, "username": me.get("username")},
        "credential": {
            "refreshed_at_before_probe": refreshed_before.isoformat() if refreshed_before else None,
            "days_since_refresh_before_probe": round((now - refreshed_before).total_seconds() / 86400, 2)
            if refreshed_before
            else None,
        },
    }

    # (a) owner timeline rate limit
    timeline = api.raw_get(f"/2/users/{user_id}/tweets", {"max_results": 5, "tweet.fields": "created_at"})
    timeline_body = _json(timeline)
    resources += len(timeline_body.get("data") or []) if isinstance(timeline_body, dict) else 0
    rate = RateLimit.from_headers(timeline.headers)
    report["timeline_rate_limit"] = {
        "status": timeline.status_code,
        "headers": _headers(timeline),
        "limit": rate.limit,
        "remaining": rate.remaining,
        "reset_at": rate.reset_at.isoformat() if rate.reset_at else None,
    }

    # (b) follower order
    probe_dir = settings.data_dir / PROBE_DIR
    previous_file, previous_ids = _previous_follower_sample(probe_dir)
    followers = api.raw_get(f"/2/users/{user_id}/followers", {"max_results": follower_sample})
    followers_body = _json(followers)
    ids = [str(u["id"]) for u in (followers_body.get("data") or [])] if followers.status_code == 200 else []
    resources += len(ids)
    report["follower_order"] = {
        "status": followers.status_code,
        "headers": _headers(followers),
        "method": "compare with the previous probe's sample (socialmgr/providers/x/probe.py)",
        "compared_with": previous_file,
        **follower_order_verdict(previous_ids, ids),
        "sample_size": len(ids),
        "sample_ids": ids,
    }
    if followers.status_code != 200:
        report["follower_order"]["error"] = followers_body

    # (c) analytics for a post at least 7 days old
    older = api.raw_get(
        f"/2/users/{user_id}/tweets",
        {"max_results": 5, "end_time": _iso(now - ANALYTICS_MIN_AGE), "tweet.fields": "created_at"},
    )
    older_posts = (_json(older).get("data") or []) if older.status_code == 200 else []
    resources += len(older_posts)
    if not older_posts:
        report["analytics"] = {
            "available": None,
            "reason": f"No post at least 7 days old was found (timeline status {older.status_code}).",
        }
    else:
        post = older_posts[0]
        created = datetime.fromisoformat(post["created_at"].replace("Z", "+00:00"))
        params = {
            "ids": post["id"],
            "start_time": _iso(created),
            "end_time": _iso(min(created + ANALYTICS_MIN_AGE, now - timedelta(minutes=1))),
            "granularity": "hourly",
            "analytics.fields": ANALYTICS_FIELDS,
        }
        response = api.raw_get("/2/tweets/analytics", params)
        body = _json(response)
        report["analytics"] = {
            "available": response.status_code == 200 and isinstance(body, dict) and "data" in body,
            "status": response.status_code,
            "headers": _headers(response),
            "request": {"path": "/2/tweets/analytics", "params": params},
            "post_created_at": created.isoformat(),
            "shape": shape(body),
            "sample": _truncate(body),
            "price": "Not reported by the API: check the developer console's usage page after this probe.",
        }

    report["approx_cost_usd"] = round(resources * OWNED_READ_USD, 4)
    probe_dir.mkdir(parents=True, exist_ok=True)
    path = probe_dir / f"probe-{now.strftime('%Y%m%dT%H%M%SZ')}.json"
    path.write_text(json.dumps(report, indent=2) + "\n")
    return report, path
