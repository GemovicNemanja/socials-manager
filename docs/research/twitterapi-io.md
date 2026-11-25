# twitterapi.io as the data-capture layer

Researched 2026-11-25. **[V]** = verified at the cited URL; **[I]** = inference. The editable cost model is in the scratchpad, `cost_model.py`.

## Verdict: partial fit, use it in a hybrid

**What twitterapi.io covers:** all public data (peers, search, replies, quotes, retweeters, follower lists and counts) plus public post metrics. Those metrics are `viewCount`, `likeCount`, `retweetCount`, `replyCount`, `quoteCount` and `bookmarkCount`.

**Cost:** about 25–35× cheaper than the official X API. Reads need no X login.

**What it cannot provide:** owner-only metrics, which are profile clicks, link clicks, video quartiles, follows/unfollows per post and hourly analytics buckets.

**Risks:** it is an undisclosed scraper. X's terms of service forbid scraping, and twitterapi.io's own terms (§04) put X-ToS compliance on the customer.

**Recommendation:** use the official API for the owner account and twitterapi.io for public data. Put both behind an `XDataProvider` interface.

## Vendor facts [V]

- **Pricing** (twitterapi.io/pricing.json, version 2026-11-25):
  - 100k credits = $1.
  - A tweet costs 15 credits ($0.15 per 1,000 tweets), with a minimum of 15 credits per call, even when the call returns nothing.
  - Calls that don't return 200 are free.
- **Rate limits:** 1 request every 5 s until the first top-up, then 20 QPS. The Scale plan allows 50 QPS and Business 500. There is no daily cap. A 429 response carries no Retry-After header.
- **Subscriptions** only add bonus credits: Starter at $29 gives 1.13×, Business 1.9×. Bonus credits expire after 31 days.
- **Operator:** Prism Digital LLC, at a Delaware registered-agent address. The domain was registered 2024-12-08. The same team runs the "kaitoeasyapi" Apify actors (31k+ users).
- **Data source:** not disclosed. The vendor claims a "zero-cookie architecture" with "no signed-in X account pool" [V]. In effect it is scraping [I].
- **Enforcement:** on 2026-11-21 X suspended scraper cookie accounts used by twscrape and Nitter. twitterapi.io says it was unaffected.
- **Uptime (status.twitterapi.io, last 30 days):** API core 99.993%, user-info 99.77%, tweet-fetch 99.96%. Slowness and free 503s cluster around :00 and :30 past the hour.
- **Reviews:** Trustpilot 4.6 from 37 reviews. One 1-star review calls it "very unreliable". The vendor pays credits for posts about it, so third-party praise is biased.
- **Contradictions in its own docs:** free sign-up credits are given as both $0.10 and "up to $1". Write-action price appears as both 200 and 60 credits. The tweets-by-ID batch limit appears as both 50 and 100. The followers minimum appears as both 60 and 15 credits.

## Endpoints

Docs: docs.twitterapi.io/api-reference/endpoint/…

| Endpoint | Returns | Price | Notes |
|---|---|---|---|
| user/info | followers, following, statusesCount, createdAt | $0.00018 per call | |
| user/batch_info_by_ids | profiles | 18 credits each under 100 IDs; **1 credit each at 100 or more** | Pad batches to 100 |
| user/last_tweets | 20 per page | 15 credits per tweet | No `since` filter, so every poll re-bills up to 20 tweets. Don't poll it. |
| user/tweet_timeline | 20 per page | 15 credits per tweet | Covers about the last 3,200 tweets. Use for backfill. |
| tweets?tweet_ids= | up to 50 IDs per call | 15 credits per tweet | Peer checkpoints |
| tweet/advanced_search | 20 per page; X operators plus `since_time:` | 15 credits per tweet; 15 minimum | Discovery: group handles as `from:a OR from:b…` |
| tweet/replies, quotes, thread_context, user/mentions | 20 per page; `sinceTime` | 15 credits per tweet | Quote results may be partial |
| user/followers / followings | newest first, up to 200 per page | 1 credit per user at a 200-user page | New-follower diff |
| user/followers_ids | up to 5k IDs per call | down to 0.45 credits per ID | Unfollower diff; a full scan of 10k followers ≈ $0.045 |
| Tweet filter rules (webhook) | rule up to 255 characters | 15 credits per pushed tweet and per empty check | |
| Twitter Stream | push, about 265 ms median | flat $149/month for 50 accounts, $299 for 150, $499 for 500 | |
| Write and login endpoints | need the user's password plus 2FA secret or cookies | 60 credits | **Never use** |

**Tweet fields returned:** `createdAt` to the second, `conversationId`, `isReply`, `isQuote`, `entities.urls`, `extendedEntities.media[]` (photo/video/gif) and the full author object, including follower count. `card`, `place` and `source` are often empty.

## Coverage

| Need | twitterapi.io | Official X API |
|---|---|---|
| Own post views and public counts | ✔ | ✔ Owned Read $0.001, deduplicated per UTC day |
| Profile clicks, link clicks, video quartiles | ✘ | ✔ with owner OAuth, posts up to 30 days old |
| Follows/unfollows per post, hourly buckets | ✘ | ✔ `/2/tweets/analytics` (price not listed) |
| New followers / unfollowers | ✔ cheap | $0.001 per follower |
| Replies, quotes and their authors | ✔ | mentions $0.001; quotes $0.005 |
| Peers, search, backfill | ✔ | $0.005 per post; 3M post reads/month cap |

## Monthly cost

> **Batch mode (2026-11-25).** The figures below assume continuous polling. v1 runs every few days instead; the batch figures (about $4.50 for 100 peers at a 3-day gap, about $10 for a workspace without on-demand research) come from `cost_model_batch.py` in this folder and are the ones the implementation plan uses (§5.3).

**Assumptions:**
- 3 posts/day, 30-day month.
- Owner: 82 snapshots per post (every 15 min to 2h, hourly to 48h, daily to day 30).
- Peer: checkpoints at 2h/6h/24h/48h/7d.
- At 1k followers, per day: 10 replies, 2 quotes, 30 new followers.

**Owner account**

| | twitterapi.io | Official | Hybrid |
|---|---|---|---|
| 1k followers | $1.53 | $9.15 | **$4.64** |
| 10k followers | $2.17 | $22.35 | **$5.29** |

**Per peer**

| Setup | Cost per peer per month |
|---|---|
| Grouped search (25 handles per query) every 15 min | **$0.089** |
| One query per peer every 15 min | $0.50 |
| Official API | $2.21 |

For 100 peers: about $8.9 on twitterapi.io vs about $221 official. For 200 peers: about $18 vs about $443.

**On-demand agent research** (20 searches × 100 tweets, 10 reply threads × 50, and 2 "last 200 posts" pulls, per day): **$13/month** on twitterapi.io vs $435 official.

**One-off backfill** (200 peers × 200 posts): $6 vs $200.

**LLM costs:**
- Labelling on Haiku 5.5 with the Batch API: about $0.00004 per post, so about $0.36/month for about 9k posts.
- Weekly agent analysis on Sonnet 5.5: about $2.60/month.

**Standard workspace** (1 owner at 1k followers, 100 peers, on-demand research, LLM):

| Setup | Monthly cost |
|---|---|
| twitterapi.io only | ≈ $26.5 (no owner-only metrics) |
| **Hybrid** | **≈ $29.6** |
| Official only | ≈ $668 |

**How the two providers bill:**
- twitterapi.io charges for every item on every poll, plus the per-call floor. Fine-grained polling of peers is therefore expensive unless you use `since_time` search with grouped handles.
- The official API deduplicates each resource per UTC day, so fine-grained polling of the owner account is free.
- Break-even: about 6.7 reads per post per day against Owned Reads, and about 33 against official post reads.

## Architecture

1. Build an `XDataProvider` interface with adapters for the official API, twitterapi.io and SocialData. Store `provider` and `fetched_at` on every snapshot.
2. **Owner, official API:**
   - Poll `GET /2/users/{id}/tweets` with public, non-public and organic metrics on the 15-min / hourly / daily schedule.
   - Pull `/2/tweets/analytics` hourly-granularity buckets at 48h, then daily buckets at days 7 and 30.
   - Read the follower count daily.
   - Request the `offline.access` scope.
3. **Owner extras, twitterapi.io:** new followers daily (newest-first page), unfollowers weekly (`followers_ids` diff), and replies/quotes via `sinceTime`.
4. **Peers, twitterapi.io:**
   - Discovery: grouped search every 15 min, with jitter, avoiding :00 and :30.
   - Checkpoints: batched `/twitter/tweets`.
   - Follower counts: `batch_info_by_ids` with 100+ IDs.
   - Backfill: `tweet_timeline`.
5. **On-demand research:** route through twitterapi.io, with daily budget caps checked against `/oapi/my/info`.
6. Never use write or login endpoints. Never share the user's cookies.

## Risks

- **Legality.** It breaks X's ToS, and X has sued scrapers. Keep the tool local and single-user, minimise stored data, get legal review before hosting it for others, and keep a one-switch fallback to the official API or SocialData ($0.20 per 1,000 items).
- **Vendor continuity.** A young LLC, and X is actively enforcing. Keep a small prepaid balance and keep the provider interface.
- **Accuracy.** Quotes can be partial and search can lag. Cross-check about 50 posts a week against official `public_metrics`. `viewCount` should be the cumulative public `impression_count`; store snapshots and compute deltas [I].
- **Reliability.** Use jitter, backoff (there is no Retry-After) and idempotent jobs. Failed calls are free.
- **Price drift.** Read `pricing.json` at startup and alert when it changes.

## Independent verification (2026-11-25)

All vendor prices, limits and terms, the X pricing (Owned Reads, dedup, the 3M cap) and every cost-table figure reproduce from the stated inputs. Corrections and gaps:

1. **Replies and quotes are modelled per post, not per day.** The model uses 10 replies and 2 quotes per post. If the real figures are per day:
   - twitterapi.io owner cost at 1k followers: ≈ $1.42 (not $1.53)
   - official API owner cost: ≈ $7.95 (not $9.15)
   - the hybrid figure barely moves
2. **The analytics price is an assumption.** The model prices `/2/tweets/analytics` at $0.005 × 3 pulls per post. X doesn't list a price; if billing is per bucket, the cost is unknown.
3. **Owned Reads apply only to the developer-app owner.** For any other user, the official owner cost at 1k followers rises to ≈ $13.5 for the timeline + $30 for the follower diff, and the hybrid to ≈ $15. Price multi-user workspaces accordingly.
4. **Failed calls are not reliably free.** Only syntax-rejected search requests (400) and priority-search 5xx responses are documented as free.
5. **Billing traps:**
   - `batch_info_by_ids` bills on the users *returned*. Request more than 100 IDs so that suspended or deleted accounts don't push the batch below 100.
   - `/twitter/tweets` bills per ID *requested*, so deleted posts still cost.
6. **Peer cost scales linearly with each peer's posting rate.** Add `-filter:replies` to `from:` searches. 25 handles per query is close to X's query-length limit.
7. **LLM token figures are assumed** (400/80 per post, 200k/20k per weekly session). At 1,000 input tokens per post, labelling is still only ≈ $0.64/month.
8. **Legal context:** X's Aug–Sep 2026 cease-and-desist letters took Nitter and XCancel offline (TechCrunch, 2026-09-15). The vendor's domain changed registrar on 2026-11-09.
9. **"25–35× cheaper"** holds for public reads. For owner metrics the gap is about 2–6×.
