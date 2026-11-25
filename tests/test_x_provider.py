"""XOwnerProvider on httpx.MockTransport: paging, classification, metrics, followers, 429s, cost."""

import dataclasses
from datetime import timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from socialmgr import plugins
from socialmgr.core.config import Settings
from socialmgr.core.interfaces import DataProvider
from socialmgr.core.types import AccountRef, Capability, CollectionBatch, CollectionRequest
from socialmgr.db.models import Cost, FollowerId, FollowerSnapshot, MetricSnapshot, Post
from socialmgr.providers.x.errors import ReauthRequired
from socialmgr.providers.x.provider import XOwnerProvider, classify, has_external_url
from socialmgr.services import pipeline
from socialmgr.services.plugins import load_plugins
from tests.x_fakes import NOW, OWN_ID, FakeX, me_body, respond, seed_owner, tweet, x_settings

TL = f"/2/users/{OWN_ID}/tweets"
FL = f"/2/users/{OWN_ID}/followers"
EMPTY: dict[str, Any] = {"meta": {"result_count": 0}}


def _ref(known: tuple[str, ...] = (), external_id: str | None = OWN_ID, account_id: int = 1) -> AccountRef:
    return AccountRef(
        account_id=account_id,
        platform="x",
        handle="@owner",
        role="owner",
        external_id=external_id,
        known_follower_ids=known,
    )


def _collect(settings: Settings, fake: FakeX, ref: AccountRef | None = None) -> CollectionBatch:
    provider = XOwnerProvider(
        x_settings(settings), http=fake.client(), tokens=lambda account_id: "tok", clock=lambda: NOW
    )
    return provider.collect(CollectionRequest(accounts=(ref or _ref(),), since=None, until=NOW))


def _page(tweets: list[dict[str, Any]], next_token: str | None = None, **includes: Any) -> dict[str, Any]:
    body: dict[str, Any] = {"data": tweets, "meta": {"result_count": len(tweets)}}
    if next_token:
        body["meta"]["next_token"] = next_token
    if includes:
        body["includes"] = includes
    return body


def _users(*ids: str, next_token: str | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"data": [{"id": i, "username": f"u{i}"} for i in ids], "meta": {}}
    if next_token:
        body["meta"]["next_token"] = next_token
    return body


def _q(request: httpx.Request) -> dict[str, str]:
    return dict(request.url.params)


def test_provider_shape_and_registry(settings: Settings) -> None:
    provider = plugins.providers.resolve("x", settings)
    assert isinstance(provider, DataProvider) and isinstance(provider, XOwnerProvider)
    assert provider.name == "x"
    assert provider.roles == frozenset({"owner"})
    assert provider.capabilities == frozenset({Capability.OWNER_METRICS, Capability.FOLLOWER_LISTS})


def test_timeline_pagination_classification_and_metrics(settings: Settings) -> None:
    h = timedelta(hours=1)
    page1 = _page(
        [
            tweet("11", NOW - 2 * h, urls=({"url": "t.co/a", "expanded_url": "https://example.com/a"},)),
            tweet("12", NOW - 3 * h, refs=("quoted",), urls=({"expanded_url": "https://x.com/a/status/5"},)),
            tweet("13", NOW - 4 * h, refs=("replied_to",), reply_to="777"),
            tweet("14", NOW - 5 * h, refs=("replied_to",), reply_to=OWN_ID, media_keys=("3_1", "7_2")),
        ],
        next_token="p2",
        media=[{"media_key": "3_1", "type": "photo"}, {"media_key": "7_2", "type": "video"}],
    )
    page2 = _page(
        [
            tweet("15", NOW - 6 * h, refs=("retweeted",), non_public=False, impressions=80),
            tweet("16", NOW - 7 * h, media_keys=("16_1",), note="a long post " * 40),
        ],
        media=[{"media_key": "16_1", "type": "animated_gif"}],
    )
    future = _page([tweet("10", NOW + h)], next_token="p1")
    fake = (
        FakeX()
        .on("/2/users/me", respond(me_body(followers=4321)))
        .on(TL, respond(future), respond(page1), respond(page2))
        .on(FL, respond(_users("f3", "f2", "f1")))
    )
    batch = _collect(settings, fake)

    tl_calls = [c for c in fake.calls if c.url.path == TL]
    assert len(tl_calls) == 3
    first = _q(tl_calls[0])
    assert first["max_results"] == "100"
    assert first["start_time"] == "2026-10-26T06:00:00Z"  # until - 30 days
    assert "non_public_metrics" in first["tweet.fields"] and "note_tweet" in first["tweet.fields"]
    assert first["expansions"] == "attachments.media_keys" and first["media.fields"] == "type"
    assert "pagination_token" not in first
    assert _q(tl_calls[1])["pagination_token"] == "p1"
    assert _q(tl_calls[2])["pagination_token"] == "p2"
    assert _q(fake.calls[0])["user.fields"] == "public_metrics"

    by_id = {p.external_id: p for p in batch.posts}
    assert "10" not in by_id  # created after the cutoff
    assert {k: p.type for k, p in by_id.items()} == {
        "11": "original",
        "12": "quote",
        "13": "reply",
        "14": "self_reply",
        "15": "repost",
        "16": "original",
    }
    assert by_id["11"].has_url and not by_id["12"].has_url
    assert by_id["14"].media_kind == "video" and by_id["16"].media_kind == "animated_gif"
    assert by_id["11"].media_kind is None
    assert by_id["16"].text.startswith("a long post")
    assert by_id["13"].in_reply_to_user_id == "777"
    assert by_id["11"].created_at == NOW - 2 * h and by_id["11"].created_at.tzinfo is not None

    m = {x.post_external_id: x for x in batch.metrics}
    assert set(m) == set(by_id)
    assert m["11"].impressions == 500  # non-public impression_count
    assert m["15"].impressions == 73  # fallback: public impression_count
    assert (m["11"].likes, m["11"].reposts, m["11"].quotes, m["11"].replies, m["11"].bookmarks) == (
        40,
        3,
        1,
        2,
        5,
    )
    assert m["11"].profile_clicks == 9 and m["11"].link_clicks == 4
    assert m["13"].link_clicks is None
    assert m["15"].profile_clicks is None
    assert all(x.impressions_source == "impression_count" and x.fetched_at == NOW for x in batch.metrics)

    (acct,) = batch.accounts
    assert (acct.external_id, acct.followers, acct.fetched_at) == (OWN_ID, 4321, NOW)
    assert acct.new_follower_ids == ("f3", "f2", "f1")  # a baseline sample: no known followers given
    assert _q(next(c for c in fake.calls if c.url.path == FL))["max_results"] == "100"
    # X never returns non-public metrics for retweets, so the repost (15) isn't a fallback caveat.
    assert not any("public impression_count" in c for c in batch.caveats)
    # 1 user lookup + 7 posts returned (including the one after the cutoff) + 3 follower records
    assert batch.cost_usd == pytest.approx(0.011)


def test_follower_paging_stops_at_known_follower(settings: Settings) -> None:
    fake = (
        FakeX()
        .on("/2/users/me", respond(me_body()))
        .on(TL, respond(EMPTY))
        .on(
            FL,
            respond(_users("n5", "n4", "n3", next_token="c2")),
            respond(_users("n2", "n1", "k1", "k0", next_token="c3")),
            respond(_users("zz")),  # must never be requested
        )
    )
    batch = _collect(settings, fake, _ref(known=("k1", "k0", "gone")))
    fl_calls = [c for c in fake.calls if c.url.path == FL]
    assert len(fl_calls) == 2
    assert _q(fl_calls[0])["max_results"] == "1000"
    assert _q(fl_calls[1])["pagination_token"] == "c2"
    assert batch.accounts[0].new_follower_ids == ("n5", "n4", "n3", "n2", "n1")
    assert batch.cost_usd == pytest.approx(0.001 * (1 + 7))


def test_no_new_followers(settings: Settings) -> None:
    fake = (
        FakeX()
        .on("/2/users/me", respond(me_body()))
        .on(TL, respond(EMPTY))
        .on(FL, respond(_users("k1", "k0", next_token="c2")))
    )
    batch = _collect(settings, fake, _ref(known=("k1",)))
    assert batch.accounts[0].new_follower_ids == ()


def test_429_on_timeline_keeps_what_it_has(settings: Settings) -> None:
    fake = (
        FakeX()
        .on("/2/users/me", respond(me_body()))
        .on(
            TL,
            respond(_page([tweet("11", NOW - timedelta(hours=1))], next_token="p2")),
            respond(status=429, remaining=0),
        )
        .on(FL, respond(_users("n1", "k1")))
    )
    batch = _collect(settings, fake, _ref(known=("k1",)))
    assert [p.external_id for p in batch.posts] == ["11"]
    assert any("rate limited" in c and "/tweets" in c for c in batch.caveats)
    assert batch.accounts[0].new_follower_ids == ("n1",)  # followers have their own limit


def test_exhausted_rate_limit_stops_paging_without_a_429(settings: Settings) -> None:
    fake = (
        FakeX()
        .on("/2/users/me", respond(me_body()))
        .on(TL, respond(_page([tweet("11", NOW - timedelta(hours=1))], next_token="p2"), remaining=0))
        .on(FL, respond(_users("n1", next_token="c2"), remaining=0))
    )
    batch = _collect(settings, fake, _ref(known=("k1",)))
    assert fake.paths().count(TL) == 1 and fake.paths().count(FL) == 1
    assert batch.accounts[0].new_follower_ids is None  # incomplete, so not counted
    assert len(batch.caveats) == 2


def test_429_on_followers_means_unknown_count(settings: Settings) -> None:
    fake = (
        FakeX()
        .on("/2/users/me", respond(me_body()))
        .on(TL, respond(EMPTY))
        .on(FL, respond(status=429, remaining=0))
    )
    batch = _collect(settings, fake, _ref(known=("k1",)))
    assert batch.accounts[0].new_follower_ids is None
    assert any("New followers not counted" in c for c in batch.caveats)


def test_429_on_user_lookup_returns_empty_batch(settings: Settings) -> None:
    fake = FakeX().on("/2/users/me", respond(status=429, remaining=0))
    batch = _collect(settings, fake)
    assert batch.accounts == () and batch.posts == () and batch.cost_usd == 0
    assert batch.caveats and "carries to the next run" in batch.caveats[0]
    assert fake.paths() == ["/2/users/me"]


def test_unauthorized_means_reauth(settings: Settings) -> None:
    fake = FakeX().on("/2/users/me", respond(status=401))
    with pytest.raises(ReauthRequired, match="socialmgr connect"):
        _collect(settings, fake)


def test_credentials_for_another_account_refused(settings: Settings) -> None:
    fake = FakeX().on("/2/users/me", respond(me_body()))
    with pytest.raises(ReauthRequired, match="not @owner"):
        _collect(settings, fake, _ref(external_id="999"))


def test_classify_and_urls_edge_cases() -> None:
    assert classify({"in_reply_to_user_id": OWN_ID}, OWN_ID) == "self_reply"
    assert classify({"referenced_tweets": [{"type": "quoted"}, {"type": "replied_to"}]}, OWN_ID) == "reply"
    assert not has_external_url(
        {"entities": {"urls": [{"media_key": "3_1", "expanded_url": "https://x.com/i"}]}}
    )
    assert not has_external_url(
        {"entities": {"urls": [{"display_url": "pic.x.com/abc", "expanded_url": "a"}]}}
    )
    assert has_external_url({"entities": {"urls": [{"expanded_url": "https://x.com/i/spaces/1"}]}})


def test_pipeline_collects_through_x_provider(settings: Settings, factory: sessionmaker[Session]) -> None:
    """Integration: the real collect step writes the provider's batch to SQLite."""
    s = x_settings(settings, handle="@Owner")
    seed_owner(factory, handle="@Owner")
    fake = (
        FakeX()
        .on("/2/users/me", respond(me_body(followers=50)))
        .on(
            TL,
            respond(_page([tweet("11", NOW - timedelta(hours=30)), tweet("12", NOW - timedelta(hours=60))])),
        )
        .on(FL, respond(_users("a", "b")))
    )
    provider = XOwnerProvider(s, http=fake.client(), tokens=lambda account_id: "tok", clock=lambda: NOW)
    base = load_plugins(s)
    active = dataclasses.replace(base, providers=(provider,))
    with factory() as session:
        run = pipeline.start_run(session, s, "cli", cutoff=NOW, now=NOW)
        summary = pipeline.collect(session, s, active, run)
        session.commit()
        assert summary.new_posts == 2 and summary.snapshots == 2
        assert summary.cost_usd == pytest.approx(0.005)
        types = {e: t for e, t in session.execute(select(Post.external_id, Post.type))}
        assert types == {"11": "original", "12": "original"}
        snaps = session.scalars(select(MetricSnapshot)).all()
        assert {(m.provider, m.impressions, m.age_s) for m in snaps} == {
            ("x", 500, 30 * 3600),
            ("x", 500, 60 * 3600),
        }
        fs = session.scalars(select(FollowerSnapshot)).one()
        assert fs.followers == 50 and fs.new_followers is None  # baseline only, no known followers yet
        cost = session.scalars(select(Cost)).one()
        assert cost.provider == "x" and cost.simulated is False
        assert set(session.scalars(select(FollowerId.external_id))) == {"a", "b"}  # baseline recorded

        # A second run the same UTC day: every read was already billed, and paging stops at "a".
        pipeline.finish_run(session, run, NOW)
        run2 = pipeline.start_run(session, s, "cli", cutoff=NOW + timedelta(hours=1), now=NOW)
        again = pipeline.collect(session, s, active, run2)
        session.commit()
        assert again.cost_usd == 0
        fs2 = session.scalars(select(FollowerSnapshot).where(FollowerSnapshot.run_id == run2.id)).one()
        assert fs2.new_followers == 0


def test_retweet_field_errors_are_expected_others_are_reported(settings: Settings) -> None:
    s = x_settings(settings)
    page = _page([tweet("11", NOW - timedelta(hours=2), non_public=False)])
    page["errors"] = [
        {
            "title": "Disallowed Resource",
            "detail": "The 'organic_metrics.impression_count' field cannot be queried for Retweets.",
        },
        {"title": "Not Found Error", "detail": "Could not find tweet with referenced_tweets.id: [9]."},
    ]
    fake = FakeX().on("/2/users/me", respond(me_body())).on(TL, respond(page)).on(FL, respond(_users()))
    provider = XOwnerProvider(s, http=fake.client(), tokens=lambda account_id: "tok", clock=lambda: NOW)
    batch = provider.collect(CollectionRequest(accounts=(_ref(),), since=None, until=NOW))
    partial = [c for c in batch.caveats if "partial error" in c]
    assert partial == [
        "The timeline returned 1 partial error(s), e.g. Could not find tweet with referenced_tweets.id: [9]."
    ]
    assert any("public impression_count" in c for c in batch.caveats)  # an own post without them
