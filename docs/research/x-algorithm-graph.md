# X ranking: audience graph, SimClusters and reputation

Source: `github.com/xai-org/x-algorithm` at commit 52d4598. Parameter defaults come from `home-mixer/params/param.rs`, which mirrors feature-switch defaults; production may override them. Simulations are in the scratchpad (`xalgo/graph/`).

## Thunder: in-network delivery

- **What it stores.** Every non-nullcast post, kept for 48h (`thunder/args.rs:48-49`).
- **How it serves a request.** Thunder reads the first 10,000 accounts a viewer follows and takes up to 50 originals and 30 replies/reposts per followed author. It sorts newest first and returns the top 1,200.
- **No sampling.** Every follower gets the post as a candidate until it falls out of their 1,200 newest. Shelf life ≈ min(48h, 1200 / hourly post volume of the accounts they follow): about 10h for a viewer following 500 accounts, 1–4h for heavy followers.
- **Replies from followed accounts are kept only if:**
  - the parent is an original post still in Thunder, or
  - it replies to a direct reply of the root post, and the viewer follows the user being replied to.
- **Out-of-network content.** Reposts are always dropped. Replies are dropped unless `EnablePhoenixOonReplies` is on (default false). **By default, replies get no For You distribution beyond the replier's own followers.**
- **Reposts by followed accounts are kept.** This is the only in-network path from a peer's audience to us.
- **No in-network quota.** Out-of-network candidates are scored ×0.75 instead.

## SimClusters: out-of-network retrieval

- **Seeds.** The viewer's recently engaged posts: at most 15 per action type, at most 90 days old. A seed needs at least 8 favs before it has an embedding.
- **Lookup per seed.** 50 clusters, 800 tweets scanned per cluster, candidates under 48h old, cosine above 0.5, at most 800 results.
- **How a tweet gets its embedding.** Each fav adds the faver's InterestedIn scores for their clusters with favScore ≥ 0.3.
  - Only favs while the tweet is under 3 days old count, with an 8h half-life.
  - A tweet enters a cluster's top-1,600 index once its score reaches 0.001.
  - **Author follower count plays no part.** A new account's post gets embedded as soon as established users fav it.
- **The real gate is the top 800 of the viewer's main cluster, not the cosine threshold.** One fav is worth about 1/√M for a cluster of M members.
- **Toy estimate, order of magnitude only.** Fresh member favs needed to reach a cluster's top 800:

  | Cluster size (members) | 2k | 10k | 50k | 200k | 1M |
  |---|---|---|---|---|---|
  | Favs needed | ~2 | ~11 | ~38 | ~80 | ~170 |

  **Pick a tight sub-niche.**
- **Batch layers, all weekly.**
  - KnownFor (cluster producers): at least 400 active followers and in the top 20M.
  - InterestedIn (consumers): needs fav edges; accounts that only follow don't move embeddings.
  - Producer embeddings: more than 100 followers or more than 100 favers (more than 15 for log-fav). These feed who-to-follow, not post retrieval.

## user-cred-v2: reputation

- **Algorithm.** Daily PageRank over follows plus 7-day fav and retweet edges.
  - Damping 0.8.
  - Dormant followers' out-edges are ignored; links between linked or alt accounts are removed.
  - Each follower splits its mass across everyone it follows.
- **Teleport vector.** 50% uniform over non-dormant Premium accounts, 50% engagement-weighted.
- **Score.** clamp(165.2 + 7.07·ln(mass), 0, 100). Doubling mass adds about 4.9 points.
- **Starting point.** A new non-Premium account with no followers has mass 0, so score 0. Premium alone gives a non-zero floor; in the simulation, mass 3.1× the average account (92nd percentile).
- **Used only to exempt accounts from automated enforcement and some labelling.** No ranking or retrieval code reads it, so cred protects against enforcement rather than boosting reach. **A new account has no shield.**
- **Simulation** (mass relative to the average account):

  | Scenario | Mass |
  |---|---|
  | 100 random followers | 1.9 |
  | 100 low-mass followers | 0.21 |
  | 10 top-1% followers | 6.9 |
  | Following all 100 back | no gain |
  | 1,000 random followers | 18.7 |

## Follow mechanics

- **Who-to-follow module.** Up to 3 accounts, at most once every 30h. The recommendation engine isn't in the repo.
- **Ranking weights.** P(follow author) is weighted 4.0; profile clicks are weighted 0.
- **Mutual-follow boost.** Original posts from mutual follows get reply weight 5 + 15 = 20. The boost launched at +20 and was cut to +15 on 2026-07-24 (`docs/BIDIRECTIONAL_BOOST_CHANGE.md`).
- **Reply ranking** uses the replier's follower count, whether the viewer follows the replier, and whether the parent author follows the replier.
- **New-author lift.** One post per request is lifted to about rank 15. The post must be an original, by an author with at most 50k followers, at most 2h old, with fewer than 200 Home impressions. It is chosen by Thompson sampling on favs per view with prior Beta(0.75, 49.25). **The post must already have been retrieved** through Phoenix or SimClusters.
- **No account-age gate** appears in the graph code.

## Implications

### Model

Model reach as **R = R_in + R_rt + R_oon**:

- **R_in** (in-network) = Σ over followers of P(opens the app within shelf life) × P(the post ranks). The mutual boost raises the second term. Follower activity and how many accounts each follower follows matter more than raw follower count.
- **R_rt** (reposts) = Σ over reposters of their own R_in.
- **R_oon** (out-of-network) = Phoenix + SimClusters + the new-author slot. It depends on niche favs in the first ~8h and on cluster size. Replies contribute about zero.

The account moves through phases:

1. Bootstrap.
2. More than 15 favers or more than 100 followers: producer embedding, with a weekly lag.
3. At least 400 active followers: eligible for KnownFor.

### Strategy for a new account

- Stay in one tight sub-niche.
- Build mutual follows with active niche peers who follow few accounts. Their ranking of our originals then uses reply weight 20 instead of 5.
- Earn reposts from those peers.
- Post when followers are online.
- Reply to root posts under 48h old, in your own words, never templated. Replies drive follow conversion, not For You reach.
- Test Premium as a reputation floor.
- Don't pad the follower count with low-quality or alt follows; they add nothing to reputation or SimClusters.

### Metrics

- **Follower composition:** each follower's follower and following counts, verified status, created_at and last post time.
- **Mutuals:** followers ∩ following.
- **Peer-panel overlap:** Jaccard similarity, plus the share of our followers who follow at least 2 panel accounts.
- **Likers and reposters with timestamps** in the first 1, 4 and 8 hours.
- **Not available from any API:** the in-network vs out-of-network impression split, and the true reputation score.
