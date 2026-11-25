# X ranking: the Phoenix retrieval and ranking models

Source: `github.com/xai-org/x-algorithm` at commit 52d4598. Paths are relative to the repo root. **[inf]** marks inference.

## No downloadable weights

- The release ships code, small single-GPU "nano" configs and a synthetic-data generator. The README says "there is no checkpoint or corpus bundle to fetch" (`phoenix/README.md:61-67`; `phoenix/TRAINING.md:99-100`).
- It needs a CUDA GPU and a Rust engine linked against `libibverbs`, so it can't run on a Mac.
- License: Apache-2.0.

## Retrieval: how out-of-network posts are found

- **Model.** A two-tower model scored by dot product. The index refreshes every 30 s (`phoenix/README.md:94-121`).
  - **User tower:** the viewer's history plus a profile token. No per-user ID embedding.
  - **Item tower:** author-ID hash embeddings plus Semantic IDs (SIDs). SIDs are 6-level × 256 residual-quantized codes of a multimodal content embedding. The tower has no post-ID embedding and no engagement counts (`xrecsys_two_tower.py:330-338`). **So a new post's retrieval vector comes from its content and author only.**
- **Home out-of-network corpus `1fav_1day`.** A post enters this index only after its first like, and only while it is under 49h old (`eventProcessing.strato:28-36,62-70`).
  - It is re-indexed at 2, 4, 8… likes.
  - Retention is 24h from creation (`phoenix-rankall/src/config/mod.rs:202`).
  - **Home out-of-network retrieval therefore only covers posts that are under 24h old and have at least one like.**
- **HOME_COLD slice.** Covers posts up to 2h old (`xrecsys_two_tower.py:313`).
- **Never indexed:** self-replies (thread continuations), Community posts and reposts (`phoenixRankAllCandidateProcessor.strato:538-566`).
- **Video index.** Native video enters the `video` index at creation if every media item is video longer than 10 s and not NSFW (`:590-595`).
- **Small-author tail window.** A `tail` window covers authors with fewer than 1,000 followers (`sid_tail_processor.rs:71-93`). It isn't wired into Home in the shipped config.

## Ranking model

- **Architecture.** 8 layers, width 2560. Viewer history up to 1,022 items, 64 candidates.
- **Candidate isolation.** Candidates attend to the user and the history, never to each other.
- **History item features:** post hash, author hash, SID, a multi-hot of the actions taken, dwell time (capped at 240 s), click-dwell, video continuation, profile-visit seconds, post age, hour, follow flags, engagement counts.
- **Candidate features:** post hash, author hash, SID, post age (hourly buckets), follow flags, **log2 counts of likes, replies, reposts, quotes and views (social proof)**, a 14-day stale flag, and the time since the viewer's last impression (`recsys_feature_prep.py`).
- **Not inputs:** text tokens, the raw embedding, language, video duration, author follower count. Content reaches the ranker only through the SID.
- **Outputs.** 64 sigmoid action heads plus 8 continuous heads.

**Heads used in scoring** (`xai-value-model/phoenix_scores.rs`):

| Kind | Heads |
|---|---|
| Positive | fav, reply, retweet, quote, photo_expand, video_open, click, open_link, profile_click, vqv (only if the video qualifies), share, share_via_dm, copy_link, dwell, quoted_click, quoted_vqv, follow_author, post_unexplored, post_video, grok_analyze |
| Negative | not_interested, block, mute, report, not_dwelled |
| Continuous | dwell_time, click_dwell_time, video_continuation × p(video_open), profile_visit_secs × p(profile_click) |

- **Bookmark** is predicted but has no scoring weight (`weights.rs:9-54`).
- **Fusion:** skip(p_not_dwelled) × Σengage × (1 + dwell) (`scoring.rs:67-261`).
- **post_unexplored exploration label.** Fires for an original post under 24h old with fewer views than 3% × followers, on an 8h half-life curve. **It can never fire for an account with 0 followers.** By default it is weighted only for in-network candidates.

## Content understanding

- **v8.2 renderer (production)** (`grox/flows/mm_emb/renderer_v82.py`). The embedder sees:
  - **author bio, verified tier, follower tier, join date, country and language**, rendered into every post;
  - the first image, or 4 video frames with duration and quality;
  - the post text;
  - the **link-card title and domain**;
  - the **ASR transcript of the video (up to 2,000 characters)**;
  - the quoted post and its author's tier.
- **v5 encoder (shipped as a port).** The public `Qwen/Qwen3-VL-Embedding-8B` model, truncated to 1024 dimensions.
- **Grox "banger screen."** A Gemma-4 26B vision-language model, run on posts with minimal traction. It outputs:
  - summary, tags and taxonomy topics
  - high-quality, spam and NSFW flags
  - a `slop_score`

  These labels feed the topic indexes and NSFW routing; they are not ranker inputs. The prompts are withheld.
- **Topic indexes.** A post keeps its topic only if at least 50, 75 or 90% of the author's last 15 original or quote posts share that category (`eventProcessing.strato:475-617`). Relevant categories: `software_development`, `ai`, `science_technology`.

## Debiasing and exploration

- Log-Q popularity correction in both ranking and retrieval.
- Per-viewer propensity re-centring (`vm-ranker/scoring/debias.rs`).
- Per-user md5-based perturbation of scoring weights.
- Cold-start lift and an author exploration bonus, computed in home-mixer.
- Author diversity decay.
- **No calibration layer and no position feature** in the Home ranker.

## Implications

### Prediction model

- **Out-of-network reach.** Model it as P(first like soon) × content/author affinity × ranker score. Treat **time to first like** as an explicit intermediate outcome, with windows at 2h, 24h and 48h (video).
- **Per-action rates.** Use a multivariate hierarchical BetaBinomial, with a time-varying author random effect and correlated actions.
- **Embedding covariates.**
  - Render each post the way v8.2 does and embed it with an open model.
  - Use cosine similarity to the account's past top-quartile posts and distance to its topic centroid.
  - Add k-means clusters at two levels as a stand-in for SID levels.
- **Within-post trajectory.** Early counts feed later reach, because social-proof counts are a ranker input.
- **Followers and timing.** Include a log(followers) × (age ≤ 24h) interaction, plus hour and day effects.

### Experiments

1. **Topic consistency:** at least 80% of posts in the niche vs a mixed feed. Measure out-of-network share and follows per 1,000 impressions.
2. **Seed the first like:** post when known engagers are online. Measure time to first like and 24h impressions.
3. **Single long post vs thread.** Self-replies are not indexed.
4. **Narrated native video over 10 s** vs GIF or screenshots. Measure views, VQV/watch time and out-of-network share.
5. **Outcome-first hook.** Measure detail expands and profile visits.
6. **Link in the post vs in the first reply.** The card title and domain are embedded.
7. **Quote-posting in-niche large accounts** vs standalone commentary.
8. **Niche-explicit bio,** before vs after. The bio is embedded into every post.
9. **Substantive replies to large in-niche accounts.** The reply ranker uses whether the root author liked the reply, whether the focal author replied, verified status and follower count (`reply_features.py:15-47`).

### Draft scorer

- Running Phoenix itself isn't feasible: there are no weights and no viewer histories.
- A proxy is feasible:
  1. Render the draft in the v8.2 format.
  2. Embed it with Qwen3-VL-Embedding-8B (about 16 GB in bf16; runs on a 48 GB Mac) or a smaller model.
  3. Score it with our own hierarchical model, plus a checklist and an LLM judge for hook strength and slop.
- Present the result as "relative to your past posts", never as X's score.

## Open questions

- Is TAIL or ZERO_FAV retrieval served in Home?
- Does a post with zero likes get a SID?
- What are the minimum-traction thresholds for the Grox screen?
- Is the production viewer history engaged-only?
- Does bookmark contribute to the Home score?
