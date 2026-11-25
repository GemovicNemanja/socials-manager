# X ranking: docs, change history, the 2023 version and public statements

Repo: `xai-org/x-algorithm`, 78 commits; line references are at `@52d4598`. Changelog files are in the scratchpad: `xalgo/history/params_changelog.txt`, plus the extractor `xalgo/scripts/params.py`.

Evidence grades used below:
- **A**: code or repo docs
- **B**: first-party statement
- **C**: credible data
- **D**: blog or unverified claim

## Docs

**`docs/BIDIRECTIONAL_BOOST_CHANGE.md`** is the only document with A/B test results.
- **Timeline:** the test started 07-10 with boost values 0/5/10/15/20. On 07-13 the value 20 went to many users. On 07-24 it was cut to 15, because people saw less World Cup discussion from accounts they don't follow. No numbers are published.
- **Mechanism:** the boost applies only to original posts by authors the viewer mutually follows. It raises the reply weight from 5 to 20.

**README milestones:**
- **01-20:** first release, with no weights.
- **05-15:** added grox, ads and mutual-follow hydrators.
- **08-13:** first release with weights and params, plus visibility filtering, SimClusters and Under the Hood.
- **10-08 / 10-13:** the ranker now predicts time spent better, and retrieval is trained on posts people read to the end.

**Engagement only counts on Home-served posts.** "Directly navigating to a post… has no ranking impact" (`param.rs:460-464`).

**Sync cadence:**
- Production defaults are mirrored to the repo daily; the sync header is stamped around 16:00 UTC and commits land a median of about 11h later.
- Experiments at 10% or more of traffic are meant to be visible.
- Production can run ahead of the repo: the mutual boost shipped before any params were public.

**Trap:** `vm-ranker/params.rs` defaults are not synced. Trust `home-mixer/params/param.rs`.

## Parameter changelog (home-mixer defaults)

| Param | 08-13 | Changes | Now |
|---|---|---|---|
| Favorite | 0.5 | 0.6 (10-13), 0.66 (10-14), 0.708 (11-05) | 0.708 |
| NotInterested | -43.2 | -47.52 (09-29), -52.272 (10-14), -53.07 (11-05) | -53.07 |
| Click | 0.4 | 0.3 (09-29), 0.274 (11-05) | 0.274 |
| ClickDwellTime | 0 | 0.4 (09-29), 0.385 (11-05) | 0.385 |
| ContDwellTime | 0.004 | 0.006 (10-13), 0.0075 (11-05) | 0.0075 |
| Dwell | 0 | 0.05 (08-25) | 0.05 |
| VQV / VideoOpen | 0.05 / 0.05 | VQV 0 (08-25); VideoOpen 0.07 → 0.02 → 0 (11-05) | 0 / 0 |
| ShareViaCopyLink | 20 | 0 (11-13), back to 20 (11-18) | 20 |
| Share | 2.0 | 2.118 (11-05) | 2.118 |
| PostUnexplored | 0.02 | 0.015 (10-07) | 0.015 |

**Never changed:** Reply 5, MutualBoost 15, Repost 1, Quote 5, DM share 5, Follow 4, OpenLink 0.2, Block -31.2, Mute -58.8, Report -234, out-of-network factor 0.75, author diversity 0.5 with floor 0.25.

**Cold-start change on 09-30 (77d431a):**
- impression threshold 1000 → 200
- follower cap 1k → 50k
- max post age 24h → 2h
- Thompson sampling turned on
- position ratio 0.85 → 0.97

**Other changes:**
- Popular-posts source turned on 10-07 (most-followed accounts only).
- FairShare across retrieval sources turned on 10-22.
- Phoenix model-head changes on 08-25 and 11-05.
- The DwellRegret value model was deleted on 09-18.
- The video carousel is still off.

## 2023 vs 2026

- **Replaced:** the MaskNet heavy ranker became the Phoenix transformer. The candidate sources became Thunder, Phoenix retrieval, SimClusters and popular posts.
- **Carried over:**
  - SimClusters v2
  - out-of-network factor 0.75
  - author diversity 0.5 / 0.25
  - TweepCred, which became user-cred-v2 and now feeds safety, not ranking
- **Weights, as multiples of a like:**

  | Signal | 2023 | 2026 |
  |---|---|---|
  | Reply | 13.5 (27×) | 5 (7×); a reply on a mutual's original is 28× |
  | Reply engaged by author | 75 (150×) | prediction no longer exists |
  | Good profile click | 12 | 0 |
  | Video playback | 0.005 | 0 |
  | Copy-link share | — | 20 (28×), new |
  | DM share | — | 5, new |
  | Dwell | — | new dwell weights |
  | Bookmarks | scored | no longer scored |

- **Verified/Premium boost:** 4× in-network and 2× out-of-network in 2023. **There is none in the 2026 For You code.**
- **Growth hacks that rely on the obsolete 2023 code:**
  - "reply to every reply (75×)"
  - "replies = 27 likes"
  - "profile clicks 12×"
  - "Premium 4×"
  - "TweepCred < 65 caps reach"
  - video and bookmark boosts
  - "1 report = N likes"

## Public statements, graded

- **Links:**
  - X's Head of Product, Oct 2025 and Apr 2026: "links are not deboosted". On 07-28/29 Musk said links haven't been penalized for over a year. **B**, consistent with the code.
  - Buffer's 2025 data contradicts this: free accounts' link posts get 0% median engagement. **C**: observational, and from before Phoenix.
- **Mutual follows:** on 07-13 X said mutual-follow data was "missing from the algo". **A+B**
- **Grok ranking:** Musk, Sep–Oct 2025: the feed goes "purely AI". Nov 2025: the Following feed is now Grok-ranked. **B** that this was said; **D** on outcomes.
- **Reach budget:** Jan 2026, users see 20–30 posts a day, so low-value replies burn reach. **B−**: the thread was reportedly deleted.
- **Time spent:** the model change is **A**; the claimed impact is **D**. "Unregretted user-seconds" is not in the code.
- **Monetization:** Original Content Rewards from 09-08 counts verified Home impressions and excludes replies. **B/C**
- **No official source exists** for a Premium boost in For You, hashtag effects or a video boost.

## Outside the repo

- **Reply ranking:** grox scores replies 0–3 with a Gemma LLM (prompts withheld). Help-center snippets describe priority tiers: followed accounts the author replied to, then verified, then everyone else. Premium tiers get growing reply prioritization. **B−** So Premium probably matters in reply sections, not in For You.
- **Search:** semantic, nearest-neighbour and follower-graph candidates.
- **Trends:** filter out low-follower and newly created accounts.
- **Communities and Articles:** nothing found.

## Implications

### Model

- **Stable:** the architecture; the reply, quote, share, follow and negative weights; the mutual boost; the out-of-network factor; author diversity; the 48h age filter.
- **Churning:** the like weight, the dwell and click weights, the video weights, copy-link, cold start (09-30), the candidate sources (10-07 and 10-22), and model retrains (08-25, 10-08, 10-13, 11-05).
- **So:** use time-varying effects with change-point priors at these dates, and allow for unannounced change-points.

**Proposed platform feature: X ranker change tracker.**
1. Fetch the repo daily after 17:00 UTC.
2. Parse `param.rs` and the README.
3. Classify each change as weight, cold start, source, model or safety.
4. Timestamp it with the sync header, not the commit time.
5. Flag it as a change-point in forecasts and experiment readouts.
6. Down-weight changes that revert within 7 days, and ignore vm-ranker defaults.

### Experiment hypotheses

1. **Mutual flywheel:** reach per mutual follower vs per one-way follower on originals.
2. **Cold-start window:** posting time, with likes per view in the first 2h as a leading indicator.
3. **Shareable reference content:** cheat sheets and benchmarks (copy-link share 20, DM share 5).
4. **Dwell / read-to-end:** long posts and Articles vs short posts.
5. **Link in the post vs link in a reply.**
6. **Video vs image vs text.** Any video lift has to come from dwell, follows or shares.
7. **Frequency:** 1, 2 or 4 originals a day, testing author decay and the 20–30 posts/day budget.
8. **Premium,** measured on reply-section metrics.
9. **Engagement source:** what share of engagement comes from For You. Avoid DM pods, since engagement outside Home doesn't count.

### Myths to drop

- the 2023 weights
- reading weight ratios as counts
- Premium 4×/2× in For You
- a link penalty in ranking
- hashtags or bookmarks as signals
- "mass reporting kills reach"
- "Grok boosts small creators" (the lift is the cold-start slot, after retrieval)
- unregretted-seconds as an objective
- "the repo equals every user's feed"

## Open questions

1. How does a 0-follower author's post become a candidate in the first place? Partly answered in `x-algorithm-phoenix.md`: Phoenix out-of-network retrieval needs at least one like within 24h, SimClusters needs favs from niche users, and the video index takes native video at creation.
2. The size of the effects in the July A/B test.
3. Whether Premium acts as an implicit feature in Phoenix.
4. What the shadow experiments Experiment18–29 change.
5. How conversation ranking is weighted.
6. Exact help-center wording.
