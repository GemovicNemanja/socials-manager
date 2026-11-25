# X ranking: suppression, safety and visibility

Code study of `github.com/xai-org/x-algorithm` at commit 52d4598 (2026-11-25). Paths are relative to the repo root. **[inf]** marks inference.

## How suppression reaches For You

- **Two safety levels.**
  - Posts from accounts the viewer follows (in-network) are checked at the `TimelineHome` level.
  - Posts from accounts the viewer doesn't follow (out-of-network) are checked at `TimelineHomeRecommendations`.
  - Reply parents and quoted posts are always checked at the out-of-network level (`home-mixer/candidate_hydrators/vf_candidate_hydrator.rs:67-106`).
- **Visibility filtering removes posts outright.** A Drop or Tombstone result takes the post out of the feed; it does not lower its score (`home-mixer/filters/vf_filter.rs:22-27`).
- **Replies inherit drops.** If a reply's parent or quoted post would be dropped, the reply is dropped too (`vf_candidate_hydrator.rs:140-170`).
- **The out-of-network level adds more drops** (`visibility-filtering/rules/levels.rs:101-108,215-220,341-384`):
  - Account labels: SpamHighRecall, Compromised, ReadOnly, ImpersonationHighPrecision, AbusiveHighRecall, DoNotAmplify, NSFW.
  - Post labels: DO_NOT_AMPLIFY, MALICIOUS_URL, SPAM_HIGH_RECALL, NSFW/gore.
- **Labels that also hide posts from followers:** SPAM, PDNA, BOUNCE, FOSNR (`levels.rs:321-339`).
- **Labels that only block notifications to non-followers** (`levels.rs:132-143,190-199,241-280`): AgathaSpamTopUser, NotGraduated, EngagementSpammer, BlinkWorst, DownrankSpamReply, DUPLICATE_CONTENT, LOW_QUALITY, RISKY_HIGH_VIZ_REPLY.
- **Viewers can opt out of notifications** from accounts that are under 72h old, use the default avatar, or have no confirmed email or phone (`rules/author_rules.rs:110-146`).

## Account labels

**SpamHighRecall** (hidden from non-followers, notifications blocked):
- A pinned post whose link resolves to a BAD or LOW_QUALITY domain at any redirect hop sets it for 7 days (`botmaker-rules/scarecrow/bot/PinnedLowQualityOrBadUrl.bot:6-44`).
- The same pinned-link check runs whenever the account follows someone (`FollowFromActorWithPinnedLowQualityOrBadUrl.bot:3-45`).
- Model labels in the enforcement service also set it (`abuse-enforcement-service/service-lib/rules/enforcement_user.yaml:39-136`):
  - `llm_slop_user`: 7 days.
  - `SpamEmbeddingMajorityPoster`: 30 days.
  - `bb1_*_bounce`: 30 days plus a liveness check.

**AgathaSpamTopUser** (notifications only; lasts 1 week, with a 6-day block on re-labelling):
- Set when the calibrated SpamSuspended score exceeds 0.99, or a spam-reports-per-like rate exceeds 0.999 (`ProductionAgatha__FilterOutOrganicNotifications.bot:9-34`).

**NSFW_HIGH_PRECISION** (lasts 7 days):
- Set when 3 of the account's last 5 posts carry the label (`safety-label-user-agg/postToUserLabelRules.strato:395-426`).

**Exemptions a new account doesn't have:**
- Accounts with user-cred ≥54, or (with no cred score) more than 25,000 followers (`derived-feature/IsHighPageRankUser.df`, `HighPageRankThreshold.df:2`).
- Government-badge accounts.
- For Grox reply labels, accounts with cred ≥66 (`grox/flows/reply_spam/constants.py:9`).

## Post labels

**Links.** Domain reputation is checked at every redirect hop, and labels are re-applied to old posts when a domain's verdict changes.
- BAD domain → SPAM (hidden everywhere).
- LOW_QUALITY domain → SPAM_HIGH_RECALL.
- UNSAFE domain → DO_NOT_AMPLIFY + MALICIOUS_URL + SEARCH_BLACKLIST.
- LOW_QUALITY link combined with an @mention of someone who doesn't follow you → SPAM_HIGH_RECALL (`LQ_Tweets_With_LQ_URL_Verdict_At_Mention_To_NonFollower.bot`).

**COPYPASTA_SPAM** comes from a clustering job that finds duplicate text in posts and replies (`BBQDuplicateTextProd.bot`, `BBQDuplicateTextRepliesProd.bot`).

**RISKY_HIGH_VIZ_REPLY** (the HideReply action) can be set three ways:
- An LLM reply-spam score of 0. Separate plans handle replies to authors at or under 250k followers and above it (`grox/flows/reply_spam/task_filter.py:17-94,185-249`).
- A Grox spam score ≥0.97, for 14 days.
- `llm_slop_post`, for 30 days.

Replies inside your own thread are skipped. The effect is no notification to the author you replied to, and [inf] lower placement in the conversation.

**SPAM_HIGH_RECALL, 30 days,** from `gibberish_post` and `fast_reply_spam_post` (`enforcement_post.yaml:46-69`). A reply is exempt when the parent post mentions the replier.

## Agatha

- **What it computes.** Per-user rates over 30 days, smoothed as `(x+0.1)/(x+favs+0.1/prior)` (`agatha/scalding/labels/rate_based_labels/RateBasedLabels.scala:29-70`):
  - abuse reports per like (reports counted over 180 days)
  - spam reports per like
  - whether the account was suspended for spam
- **Denominator.** Only likes from users with no follow link in either direction count (`labels/favs/Favs.scala:15-68`).
- **How it predicts.** It infers those rates from who you associate with: the accounts you like, follow, reply to, retweet, quote and mention, plus your hashtags, bio words, signup client, IP subnet and follower counts (`data/ActiveUserFeaturesJob.scala:18-41`).
- **What it affects.** Not For You directly. It feeds notification blocks and the AGATHA_SPAM post label (1 week; [inf] removes posts from search).

## Bot detection (`bdsm/`, the Behavioral Detection Sequence Model)

- **Model.** A transformer over the account's last 512 actions, with timing features (`bdsm/README.md:19-34`):
  - actions less than 1 second apart
  - streaks of actions on the same author
  - liking or replying to a post the account was never shown
  - unusually regular gaps between actions
- **Outputs.** FollowBot (follow/unfollow cycles, API-only bots, growth-service bots), EngagementAmplifier (follow → reply pipelines) and ReplySpamBot. A LegitimateUser output explicitly covers POWER_USER_API_CLIENT (`runtime/heads.py:16-111`).
- **Enforcement rules** (`runtime/score_results_sink_focal.py`):
  - Accounts where at least 50% of actions are new posts skip hard enforcement.
  - FollowBot enforcement waits until the account is 48h old.
  - Borderline accounts get a captcha or liveness check, not a suspension.
  - Spam challenges apply only when at least 90% of posting comes from one official X app. The code notes: "Legit API services post via third-party OAuth apps and never pass this gate."
- **Thresholds.** Real thresholds are redacted.

## Under the Hood report

- **Access.** Owner-only, through internal GraphQL, not the public API. It can be downloaded as JSON, so the platform needs a manual upload path.
- **Eligibility.** The account must be at least 1 year old and have posted 10+ times in the prior month. Reports are monthly and arrive 10 days after the month ends. **A new account won't get one for a year.**
- **Contents.** Per post label: count, percentage and up to 3 example post IDs (none shown when more than 5% of posts carry the label). Per account label: days and percentage of days.
- **Not shown:** AgathaSpamTopUser, RISKY_HIGH_VIZ_REPLY, COPYPASTA_SPAM, AGATHA_SPAM, DUPLICATE_CONTENT, LOW_QUALITY, and the account labels that only block notifications.

## Implications

### Model

- Treat suppression as a hidden regime that switches on and off:
  - E[reach] = P(not suppressed) × reach when eligible.
- The signature is out-of-network impressions dropping to about zero while follower engagement stays normal.
- Expect suppression to last 7, 14 or 30 days.
- Model replies separately from posts. Include the parent author's size (above or below 250k followers) and risk, because replies inherit drops.

### Safe-growth ruleset

1. Never repeat text or use templates, in posts or replies.
2. Write replies specific to each thread. No links in replies to strangers, and at most about 2–3 replies in a row to one author [inf].
3. Use the API only to publish your own scheduled posts. Like and reply from a real client, after reading the post.
4. Add ±10–20 minutes of jitter to scheduled posts. No bursts.
5. No follow/unfollow cycles. Follow slowly, and don't chain follow → like → reply on the same person.
6. Link only reputable domains, with no unknown shorteners. Never pin a link to an untested domain. Don't combine a link with @mentions of non-followers.
7. Set an avatar and confirm email and phone. Expect weak notification reach for the first 72 hours.
8. Avoid spammy clusters and hashtags. Don't reply to posts that look labelled.
9. In guardrail experiments, change one risk factor at a time, stop when a change-point appears, and wait 7–30 days before concluding.

### Metrics

- Impressions split by follower vs non-follower.
- Likes from non-followers.
- Profile visits and follows.
- Whether the person you replied to saw or engaged with the reply.
- Distribution of gaps between your own actions.
- Visibility probes from a consenting account that doesn't follow you.
- The Under the Hood JSON, once the account is eligible.

## Open questions

- Thresholds are redacted or mocked (for example `12.34`).
- No released code sets DoNotAmplify (account), AbusiveHighRecall, NotGraduated, EngagementSpammer, BlinkWorst, DownrankSpamReply, DUPLICATE_CONTENT or LOW_QUALITY.
- Not in the release: what consumes COPYPASTA_SPAM, AGATHA_SPAM and the reply-ranking score.
- The Grox prompts are withheld.
