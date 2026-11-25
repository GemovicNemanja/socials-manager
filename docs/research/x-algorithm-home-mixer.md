# X ranking: the home-mixer For You pipeline

Source: `xai-org/x-algorithm` at commit 52d4598.

- **Where the defaults come from.** Defaults in `home-mixer/params/param.rs` are "mirrored from config feature-switch defaults; last sync 2026-11-24". The live config is not in the repo.
- **Correction (verified):** every scoring weight is in `home-mixer/params/param.rs`, the authoritative mirror. vm-ranker defaults are stale fallbacks. Since 09-24 the OON factor, author-diversity decay and floor, the OON-on-in-network-replies flag and DPP settings exist only as vm-ranker defaults, so they are unverified live values.

Simulation files are in the scratchpad (`xalgo/homemixer/`).

## Request path

1. **Outer pipeline.** Blends posts, ads, who-to-follow, prompts and frames.
2. **Inner post pipeline** (`candidate_pipeline/phoenix_candidate_pipeline.rs:289-556`):
   1. query hydration
   2. sources
   3. hydrators
   4. 23 filters
   5. scorers in order: Phoenix → ImmersiveVideo → VMRanker (which includes cold start) → PageDecode → PageRL
   6. top 50
   7. visibility filter and conversation dedup
   8. **35 posts served**
3. **FairShare** caps the ranking pool at 2,800 candidates, split across sources by volume.
4. **Cache.** The top 750 candidates are cached for 180 s and reused when at least 500 remain. On a cache hit:
   - retrieval and Phoenix are skipped;
   - engagement counts are refreshed (60 s cache);
   - the cold-start draw is re-done.

## Sources

| Source | Default | Notes |
|---|---|---|
| Thunder (in-network) | 1,200 | |
| Phoenix retrieval | 1,000 + 200 "HomeCold" | A "ZeroFav" dataset exists too |
| SimClusters | ≤800 | ≤48h old, similarity >0.5; matches like-based embeddings |
| Popular posts | 500 | Originals from the top 0.005% of authors by followers only. Unreachable for us. |
| TweetMixer, MOE, SID, PopularVideo | off | |

**For a 0-follower account, Phoenix retrieval is the gate.**

## What the ranker sees per post

- Counts: likes, reposts, quotes, replies, views, bookmarks.
- Post type and language: language, has_media, is_quote, is_reply, is_retweet.
- Author and follow graph: author follower count, whether the viewer follows the author, whether the author follows the viewer.
- Content features: has_video, max video duration, has_photo, media count, weighted text length (a URL counts as 23), newline count, **has_url**.

There is **no hand-coded link penalty or media boost**. Any effect is learned inside Phoenix.

## Filters that matter

| Filter | What it does |
|---|---|
| Age | Drops posts older than 48h, in-network included |
| Out-of-network reply/repost | Always drops these (the flag that would allow them is false); also drops replies with no parent chain |
| Seen | Permanent, via a bloom filter of the user's Home impressions |
| Served | Drops posts served in the last 10 minutes |
| Mute/block | Drops posts from muted or blocked authors |
| Conversation dedup | Keeps only the best-scoring post per conversation |
| Visibility | Drops posts with a Drop or Tombstone action |

Off by default: the new-user minimum-engagement filter, the holdouts and ZeroFav codivert.

## Score formula

Sources: `xai-value-model/scoring.rs:112-482` and `vm-ranker/scoring/value_model.rs:75-180`.

**Weighted sum:** w = Σ W_h·P_h, with these weights:

| Action | Weight |
|---|---|
| favorite | 0.708 |
| reply | 5 (+15 when the post is an original AND the author and viewer follow each other) |
| retweet | 1 |
| quote | 5 |
| share | 2.118 |
| share_via_dm | 5 |
| copy_link | 20 |
| follow_author | 4 |
| click | 0.274 |
| open_link | 0.2 |
| photo_expand | 0.05 |
| dwell | 0.05 |
| dwell_time | 0.0075/s |
| click_dwell_time | 0.385 |
| quoted_click | 0.05 |
| post_unexplored | 0.015 (in-network only) |
| video_open | 0 |
| vqv | 0 (only counted for video longer than 10 s) |
| profile_click | 0 |
| not_interested | −53.07 |
| block | −31.2 |
| mute | −58.8 |
| report | −234 |
| not_dwelled | −0.02 |

**Steps after the weighted sum:**
1. **Base:** base = w + 0.001 when w ≥ 0; negative values are squashed into (0, 0.001).
2. **Cold start:** for one post per request, base = max(base, the base score at rank 15).
3. **Final:** final = base × AD(k) × OON.
   - **AD(k), author diversity** = 0.75·0.5^k + 0.25, where k is the number of the author's own posts ranked above this one. That gives 1, 0.625, 0.4375… with a floor of 0.25.
   - **OON** = 0.75 for out-of-network posts and for in-network replies and reposts.
4. **DPP diversity:** takes the top 150, makes a greedy diverse pick of 50 (θ 0.65), and zeroes the rest.

**Off by default:** page decode, page-RL exploration, weight perturbation, user debias, author exploration bonus.

**30% shadow traffic** goes to experimental Phoenix clusters. It is shadow only.

## Cold-start scorer (`scorers/author_cold_start.rs`)

**Eligibility.** A post qualifies only if all of these hold:
- it is an original (quote posts count as originals);
- the author has 50,000 followers or fewer;
- it is 2h old or younger;
- it has fewer than 200 Home views (a post with no view entry yet is ineligible);
- it is not in the bottom 3% of the pool.

**Selection: Thompson sampling.**
- Each eligible post draws from Beta(0.75 + x, 49.25 + n − x), where n is Home views and x = min(total likes, n). Likes from every surface count.
- The top 2 draws go to a tie-break, won by the higher weighted score.

**Lift.** The winner's score is raised to the rank-15 score:
- before the diversity and OON multipliers are applied;
- for one post per request, re-drawn on every request.

## Implications

### Model

- **Post type is the biggest observable feature.** Replies and reposts from accounts the viewer doesn't follow get zero For You reach, and in-network replies get ×0.75. Only originals and quotes can win the cold-start slot.
- **Age buckets:** 0–2h (cold-start window), 2–48h (organic), 48h hard cutoff. Expect a kink at 2h or about 200 Home views, whichever comes first.
- **Early like rate:** use the posterior mean (0.75 + likes)/(50 + views). Total impressions overstate Home views.
- **Other features:**
  - log engagement counts (social proof, a direct model input);
  - content features matching Phoenix's;
  - the number of the author's own posts in the last 48h;
  - the number of mutual follows.
- **Weight ratios:** per unit of predicted probability, a copy-link share ≈ 28 likes, and a reply or quote ≈ 7 likes.
- **Not observable from outside:** whether the post was retrieved, per-viewer probabilities, how many posts compete for the slot, Home views as a separate count, and the live config.

### Experiments

1. **Early likes:** 1–2 likes in the first 25–50 views move the like-rate posterior a lot. At 25 views the mean goes from 1.0% to 2.3%.
2. **Likes from outside Home** (profile visitors, external shares) also feed the sampler. No coordinated engagement.
3. **Spacing:** originals at least 3–4h apart vs bursts.
4. **Quote with commentary vs a reply** with the same text.
5. **Link in the post vs in the first self-reply.** The has_url effect is learned, so its direction is unknown.
6. **Video:** over 10 s vs 10 s or under vs image vs text.
7. **Single post vs thread.** Conversation dedup shows only one post per conversation.
8. **Mutual follows** in the niche (×4 reply weight).
9. **Posting hour.** The chance of winning the slot is roughly 1/K, where K is the number of competing eligible posts.
10. **Re-posting the same content as a new post.** It gets a fresh window, but DPP may suppress near-duplicates.

### Capture

- **Checkpoints:** 5, 15, 30, 60, 90 and 120 min, then 4, 8, 24, 48 and 72h.
- **Per-post attributes:** post type, has_url, media and duration, text length, newlines, gap since our previous post, our posts in the last 48h, followers and mutuals at posting time.
- Sampling finer than 1–3 minutes adds little, because counts are cached for 60 s and candidates for 180 s.

## Simulation (assumptions stated in the scratchpad)

**Chance of winning the cold-start slot in one request against 20 competitors:**

| Home views | 0–1% like rate | 1.5% | 1.5–2% | 3% | 5% | 8% |
|---|---|---|---|---|---|---|
| 0 | 0.045 | 0.045 | 0.045 | 0.045 | 0.045 | 0.045 |
| 50 | 0.007 | — | 0.04 | 0.11 | — | 0.28 |
| 100 | 0.001 (at 0%) | 0.036 | — | — | 0.22 | 0.39 |

At 0 views every post has the same chance, because only the prior counts. With 50 competitors, all chances are about 2.5× lower.

**Two-hour lifecycle** (20 retrieval requests per minute):

| True like rate | Cold-start impressions |
|---|---|
| 0% | 33 |
| 1% | 47 |
| 2% | 60 |
| 4% | 126 |
| 8% | 182 |

At 8% the post reaches the 200-view cap in a median of 68 minutes. Read the ratios, not the absolute numbers: there is about a 5× spread across like rates.

## Open questions

- Live config values: the nine missing weights, DPP top_k, and any cold-start overrides.
- Whether a brand-new post has a Home view entry before its first Home view.
- How the HomeCold and ZeroFav retrieval sets are selected.
- How replies are ordered in conversation view.
- What Phoenix has learned about has_url, video, text length and newlines.
