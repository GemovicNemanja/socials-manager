# Verification of the X algorithm notes (47 claims, repo 52d4598, full history)

Most claims hold. The corrections below take precedence over the individual notes.

1. **Weights.** click = **0.274** and share = **2.118** (not 0.3 / 2). All weights are in `home-mixer/params/param.rs`, which is authoritative; the vm-ranker defaults are stale.
2. **Settings that exist only as vm-ranker defaults since 09-24:**
   - the OON factor 0.75
   - author-diversity decay 0.5 and floor 0.25
   - the flag applying the OON factor to in-network replies/reposts
   - DPP θ 0.65 and its pool of 150
   - the video-continuation and profile-visit weights

   Treat these as unverified live values. The "never changed" list holds in the mirrored defaults only up to 09-23.
3. **The OON factor (×0.75) also applies to in-network replies and reposts** (`xai-value-model/scoring.rs:327-336`). In-network originals are not penalised.
4. **Fusion.** The default is a weighted sum. "skip × Σengage × (1+dwell)" is a non-default multiplicative mode.
5. **Cold start.** The lift goes to 0-based index 15, i.e. the 16th-highest score. For viewers who don't follow the author it is then multiplied by 0.75 (and by AD), so the post lands below 16th. The view counter is usually `Some(0)`, so posts with no views are typically eligible.
6. **Pipeline.** It has 23 filters, not 21. DPP top_k 50 is a vm-ranker command-line default.
7. **Age filter history.** 24h → 48h on 09-17, then 48h → 2h on 09-30. VideoOpen went 0.05 → 0.07 on 08-25.
8. **Bookmarks.** There is no evidence bookmarks were scored in 2023.
9. **Engagement outside Home does count.** Likes from any surface put a post into the 1-like index, feed the cold-start Beta and feed the ranker's log-count features. The param.rs comment only concerns training labels. Avoid engagement pods for safety reasons, not because the likes are ignored.
10. **Retrieval before the first like is not ruled out:**
    - Every original goes into a `post_creation` index (24h retention, no like required).
    - home-mixer recognises a `ZeroFav` dataset; its codivert filter is off by default.
    - Native video over 10 s enters the video and AI-video indexes at creation; Home maps the AI_VIDEO_*_2DAY datasets.
    - Whether production Home uses any of these is unknown.

    Model P(retrieved before the first like) as small but non-zero. Test it: compare 0–2h impressions on posts with 0 likes, text vs native video over 10 s.
