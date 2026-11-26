import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

// The public journal (M4): the static site built to web/dist-journal, served for the owner's preview
// at /journal/ by `socialmgr serve`. With no data.json in the build it reads /api/journal?preview=true,
// so drafts show, marked. The experiment backend (playwright.config.ts) holds the running E03 x E06
// factorial plus two weekly entries (`socialmgr fixture --journal-demo`: last week published, this
// week a draft). Every number checked here is read from the API first; the experiment specs share
// this backend and may add experiments, so nothing depends on exact experiment counts.
const EXP = "http://127.0.0.1:8796";
const PLAIN = "http://127.0.0.1:8798";
const EMPTY = "http://127.0.0.1:8799";

const int = (v: number) => new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 }).format(Math.round(v));
const signed = (v: number) => (v === 0 ? "±0" : `${v > 0 ? "+" : "−"}${int(Math.abs(v))}`);
const effect = (x: { effect_ratio: number; effect_ci80: [number, number] | null }) =>
  x.effect_ci80
    ? `${x.effect_ratio.toFixed(2)}× [${x.effect_ci80[0].toFixed(2)}, ${x.effect_ci80[1].toFixed(2)}]`
    : `${x.effect_ratio.toFixed(2)}×`;

async function journal(request: APIRequestContext, base: string, preview = true) {
  const response = await request.get(`${base}/api/journal?preview=${preview}`);
  expect(response.ok()).toBeTruthy();
  return response.json();
}

/** Fails the test if the page ever opens a dialog (the raw <script> in the demo entry must stay text). */
function noDialogs(page: Page) {
  page.on("dialog", (d) => {
    throw new Error(`unexpected dialog: ${d.message()}`);
  });
}

test.describe("Public journal on the experiment demo", () => {
  test.describe.configure({ mode: "serial" });
  test.use({ baseURL: EXP });

  test("the preview is served at /journal/ and /journal redirects there", async ({ request }) => {
    const bare = await request.get(`${EXP}/journal`, { maxRedirects: 0 });
    expect([302, 307]).toContain(bare.status());
    expect(bare.headers()["location"]).toBe("/journal/");
    const page = await request.get(`${EXP}/journal/`);
    expect(page.ok()).toBeTruthy();
    expect(await page.text()).toContain('property="og:title"');
    // No data.json in the build: the page falls back to the preview API.
    expect((await request.get(`${EXP}/journal/data.json`)).status()).toBe(404);
  });

  test("home: stats, follower chart with experiment starts, recent experiments and notes with the draft marked", async ({ page, request }) => {
    const j = await journal(request, EXP);
    expect(j.entries.length).toBe(2);
    noDialogs(page);
    await page.goto("/journal/#/");
    await expect(page.getByRole("heading", { level: 1, name: j.site.headline })).toBeVisible();
    await expect(page).toHaveTitle(`${j.site.handle} · ${j.site.tagline}`);
    await expect(page.getByText(`Week ${j.site.week_number} of growing my account in public`)).toBeVisible();
    await expect(page.getByText("Preview — drafts shown")).toBeVisible();

    const nav = page.getByRole("navigation", { name: "Journal sections" });
    await expect(nav.getByRole("link", { name: "Journal" })).toHaveAttribute("aria-current", "page");
    await expect(nav.getByRole("link", { name: `Experiments ${j.experiments.length}` })).toBeVisible();
    await expect(page.getByRole("link", { name: "Follow on X" }).first()).toHaveAttribute("href", j.site.x_url);

    const stats = page.getByRole("group", { name: "Totals so far" });
    await expect(stats).toContainText(int(j.stats.followers_now));
    await expect(stats).toContainText(`followers, from ${int(j.stats.followers_start)} on`);
    await expect(stats).toContainText(`${int(j.stats.posts_scored)}of my posts scored against forecast`);
    await expect(stats).toContainText(`${Math.round(j.stats.hit_80 * 100)}%inside the 80% range`);
    await expect(stats).toContainText(
      new RegExp(`${j.stats.experiments_total}experiments?, ${j.stats.experiments_with_effect} found an effect`),
    );

    const chart = page.getByRole("region", { name: "My followers since launch" });
    const last = j.followers[j.followers.length - 1];
    await expect(chart.getByRole("img")).toHaveAttribute("aria-label", new RegExp(`to ${int(last.value)} on`));
    await expect(chart.getByRole("img")).toHaveAttribute("aria-label", /experiment starts? marked/);
    await expect(chart.getByText("Experiment started")).toBeVisible();

    const recent = page.getByRole("region", { name: "My recent experiments" });
    for (const x of j.experiments.slice(0, 3)) {
      const row = recent.getByRole("link", { name: new RegExp(`^${x.key}`) });
      await expect(row).toContainText(x.name.slice(0, 12));
      await expect(row).toContainText(x.progress_label);
      if (x.effect_ratio != null) await expect(row).toContainText(effect(x));
    }

    const notes = page.getByRole("region", { name: "Weekly notes" });
    const [draft, published] = j.entries;
    expect(draft.draft).toBe(true);
    expect(published.draft).toBe(false);
    const draftCard = notes.getByRole("article").filter({ hasText: draft.title });
    await expect(draftCard.getByText("Draft — not public")).toBeVisible();
    const publishedCard = notes.getByRole("article").filter({ hasText: published.title });
    await expect(publishedCard.getByText("Draft — not public")).toHaveCount(0);
    await expect(publishedCard).toContainText(published.summary.slice(0, 40));
    await expect(publishedCard).toContainText(`${signed(published.stats.followers_delta)} followers`);

    await expect(page.getByRole("group", { name: "About these numbers" })).toContainText(
      j.method.sections[0].facts.find((f: { label: string }) => f.label === "Source").value,
    );
    await page.screenshot({ path: "e2e/.results/journal-home.png", fullPage: true });
  });

  test("entry: stats, safe markdown body, posts against forecast, experiments mentioned", async ({ page, request }) => {
    const j = await journal(request, EXP);
    const entry = j.entries.find((e: { draft: boolean }) => !e.draft);
    noDialogs(page);
    await page.goto("/journal/#/");
    await page.getByRole("link", { name: entry.title }).click();
    await expect(page).toHaveURL(new RegExp(`#/notes/${entry.slug}$`));
    await expect(page.getByRole("heading", { level: 1, name: entry.title })).toBeVisible();
    await expect(page).toHaveTitle(`Week ${entry.week_number}: ${entry.title} · ${j.site.handle}`);

    const stats = page.getByRole("group", { name: "This week" });
    await expect(stats).toContainText(signed(entry.stats.followers_delta));
    if (entry.stats.followers_expected) {
      const [lo, hi] = entry.stats.followers_expected;
      await expect(stats).toContainText(`followers, forecast ${signed(lo)} to ${signed(hi)}`);
    }
    await expect(stats).toContainText(`${entry.stats.posts_in_80} / ${entry.stats.posts_scored}`);

    // Markdown: headings, lists, emphasis, links and code render; raw HTML stays text.
    const body = page.getByRole("region", { name: "Note text" });
    await expect(body.getByRole("heading", { level: 2, name: "What happened" })).toBeVisible();
    await expect(body.getByRole("heading", { level: 2, name: "Next week" })).toBeVisible();
    await expect(body.locator("strong", { hasText: "outcome-first hook" })).toBeVisible();
    await expect(body.locator("em", { hasText: "image" })).toBeVisible();
    await expect(body.locator("ul > li")).toHaveCount(3);
    await expect(body.locator("ol > li")).toHaveCount(2);
    await expect(body.getByRole("link", { name: "thread on forecasting" })).toHaveAttribute("href", "https://x.com/your_account");
    await expect(body.locator("code", { hasText: "why I tested it" })).toBeVisible();
    await expect(body.getByText('<script>alert("raw HTML is shown as text, never run")</script>')).toBeVisible();
    await expect(page.locator("main script")).toHaveCount(0);

    const posts = page.getByRole("list", { name: "Posts against their forecast" });
    await expect(posts.getByRole("listitem")).toHaveCount(entry.posts.length);
    await expect(page.getByText(`top ${entry.posts.length} of ${entry.posts_total}, by 48h impressions`)).toBeVisible();
    const top = entry.posts[0];
    await expect(posts.getByRole("listitem").first()).toContainText(int(top.i48));
    await expect(posts.getByRole("link").first()).toHaveAttribute("href", top.url);
    await expect(posts.getByRole("img").first()).toBeVisible(); // the range strip

    const mentioned = page.getByRole("region", { name: "Experiments mentioned" });
    for (const key of entry.experiment_keys) await expect(mentioned.getByRole("link", { name: new RegExp(`^${key}`) })).toBeVisible();
    await expect(page.getByRole("button", { name: "Copy link" })).toBeVisible();
    await page.screenshot({ path: "e2e/.results/journal-entry.png", fullPage: true });

    await page.getByRole("link", { name: "All weekly notes" }).click();
    await expect(page).toHaveURL(/#\/$/);
    await expect(page.getByRole("heading", { level: 1, name: j.site.headline })).toBeVisible();
  });

  test("draft entry: marked as not public in the preview", async ({ page, request }) => {
    const j = await journal(request, EXP);
    const draft = j.entries.find((e: { draft: boolean }) => e.draft);
    await page.goto(`/journal/#/notes/${draft.slug}`);
    await expect(page.getByRole("heading", { level: 1, name: draft.title })).toBeVisible();
    await expect(page.getByRole("article").getByText("Draft — not public")).toBeVisible();
    // The previous note (the published one) is linked below.
    const published = j.entries.find((e: { draft: boolean }) => !e.draft);
    await expect(page.getByRole("navigation", { name: "More notes" }).getByRole("link", { name: published.title })).toBeVisible();
  });

  test("experiments: results by group, effects at a glance, every row opens its write-up", async ({ page, request }) => {
    const j = await journal(request, EXP);
    await page.goto("/journal/#/");
    await page.getByRole("navigation", { name: "Journal sections" }).getByRole("link", { name: /Experiments/ }).click();
    await expect(page).toHaveURL(/#\/experiments$/);
    await expect(page.getByRole("heading", { level: 1, name: new RegExp(`^${j.experiments.length} experiments? so far`) })).toBeVisible();
    await expect(page).toHaveTitle(`Experiments · ${j.site.handle} · ${j.site.tagline}`);
    const running = j.experiments.filter((x: { result: string }) => x.result === "running" || x.result === "scheduled");
    await expect(page.getByRole("group", { name: "Experiments by result" })).toContainText(`${running.length}running now`);

    const all = page.getByRole("region", { name: "All experiments" });
    const group = all.getByRole("group", { name: "Running" });
    for (const x of running) {
      const row = group.getByRole("link", { name: new RegExp(`^${x.key}`) });
      await expect(row).toContainText(x.progress_label);
      await expect(row).toContainText(x.effect_ratio != null ? effect(x) : "too early");
      await expect(row).toContainText("Running");
    }
    const forest = page.getByRole("region", { name: /Effects at a glance/ });
    const estimated = j.experiments.filter((x: { effect_ratio: number | null }) => x.effect_ratio != null);
    for (const x of estimated) await expect(forest.getByRole("img")).toHaveAttribute("aria-label", new RegExp(`${x.key} ${effect(x).replace(/[[\]]/g, "\\$&")}`));
    await page.screenshot({ path: "e2e/.results/journal-experiments.png", fullPage: true });
  });

  test("experiment: question, answer, stats, why, dot chart per arm, setup, outcome and related", async ({ page, request }) => {
    const j = await journal(request, EXP);
    const x = j.experiments.find((e: { key: string }) => e.key === "E03");
    noDialogs(page);
    await page.goto("/journal/#/experiments");
    await page.getByRole("link", { name: /^E03/ }).first().click();
    await expect(page).toHaveURL(/#\/experiments\/e03$/);
    await expect(page.getByRole("heading", { level: 1, name: x.question })).toBeVisible();
    await expect(page).toHaveTitle(`E03: ${x.question} · ${j.site.handle}`);
    await expect(page.getByText(x.answer)).toBeVisible();
    const stats = page.getByRole("group", { name: "Result" });
    await expect(stats).toContainText(`${x.effect_ratio.toFixed(2)}×`);
    await expect(stats).toContainText(int(x.posts_matured));
    await expect(stats).toContainText(`${Math.round(x.p_better * 100)}%`);

    // The owner's "Why I tested it" note (journal demo) renders as markdown.
    await expect(page.getByRole("region", { name: "Why I tested it" }).locator("strong", { hasText: "what they get" })).toBeVisible();

    const chart = page.getByRole("region", { name: "Every post, as a share of its own forecast" });
    await expect(chart.getByRole("img")).toHaveAttribute("aria-label", new RegExp(`^${x.points.length} posts`));
    for (const arm of x.arms) await expect(chart).toContainText(`typical post ${arm.typical_ratio.toFixed(2)}× forecast · ${arm.n} posts`);

    const setup = page.getByRole("group", { name: "Setup" });
    for (const f of x.setup) await expect(setup).toContainText(`${f.label}${f.value}`);
    const sure = page.getByRole("group", { name: "How sure" });
    await expect(sure).toContainText(`p = ${x.randomization_p.toFixed(2)}`);
    const note = j.entries.find((e: { slug: string }) => e.slug === x.note_slug);
    await expect(sure.getByRole("link", { name: new RegExp(note.title) })).toHaveAttribute("href", `#/notes/${note.slug}`);
    const related = page.getByRole("region", { name: "Related experiments" });
    for (const key of x.related_keys) await expect(related.getByRole("link", { name: new RegExp(`^${key}`) })).toBeVisible();
    await page.screenshot({ path: "e2e/.results/journal-experiment.png", fullPage: true });
  });

  test("method: numbered sections with facts and coverage meters", async ({ page, request }) => {
    const j = await journal(request, EXP);
    await page.goto("/journal/#/");
    await page.getByRole("link", { name: "Method", exact: true }).click();
    await expect(page).toHaveURL(/#\/method$/);
    await expect(page.getByRole("heading", { level: 1, name: j.method.headline })).toBeVisible();
    for (const s of j.method.sections) {
      const section = page.getByRole("region", { name: s.title });
      await expect(section.getByRole("heading", { level: 2, name: `${s.number} · ${s.title}` })).toBeVisible();
      await expect(section).toContainText(s.body.slice(0, 60));
      for (const f of s.facts) await expect(section).toContainText(`${f.label}${f.value}`);
      await expect(section.getByRole("meter")).toHaveCount(s.coverage.length);
      for (const c of s.coverage) {
        const meter = section.getByRole("meter", { name: c.label });
        await expect(meter).toHaveAttribute("aria-valuenow", String(Math.round((c.value ?? 0) * 100)));
      }
    }
    await page.screenshot({ path: "e2e/.results/journal-method.png", fullPage: true });
  });

  test("hash routes: unknown pages say so, back and forward work", async ({ page, request }) => {
    const j = await journal(request, EXP);
    await page.goto("/journal/#/notes/week-99");
    await expect(page.getByRole("heading", { level: 1, name: "There's no weekly note here" })).toBeVisible();
    await page.goto("/journal/#/nowhere");
    await expect(page.getByRole("heading", { level: 1, name: "There's no page here" })).toBeVisible();
    await page.goto("/journal/#/method");
    await page.getByRole("link", { name: "Journal", exact: true }).click();
    await expect(page.getByRole("heading", { level: 1, name: j.site.headline })).toBeVisible();
    await page.goBack();
    await expect(page.getByRole("heading", { level: 1, name: j.method.headline })).toBeVisible();
  });

  test("a published site reads ./data.json: no drafts, no preview badge", async ({ page, request }) => {
    const published = await journal(request, EXP, false);
    expect(published.entries.every((e: { draft: boolean }) => !e.draft)).toBe(true);
    let previewCalled = false;
    await page.route("**/journal/data.json", (route) => route.fulfill({ json: published }));
    await page.route("**/api/journal**", (route) => {
      previewCalled = true;
      return route.continue();
    });
    await page.goto("/journal/#/");
    await expect(page.getByRole("heading", { level: 1, name: published.site.headline })).toBeVisible();
    const notes = page.getByRole("region", { name: "Weekly notes" });
    await expect(notes.getByRole("article")).toHaveCount(published.entries.length);
    await expect(page.getByText("Draft — not public")).toHaveCount(0);
    await expect(page.getByText("Preview — drafts shown")).toHaveCount(0);
    expect(previewCalled).toBe(false);
  });
});

test.describe("Public journal empty states", () => {
  test("plain fixture: followers but no experiments and no notes yet", async ({ page, request }) => {
    const j = await journal(request, PLAIN);
    expect(j.experiments).toEqual([]);
    expect(j.entries).toEqual([]);
    await page.goto(`${PLAIN}/journal/#/`);
    await expect(page.getByRole("heading", { level: 1, name: j.site.headline })).toBeVisible();
    await expect(page.getByRole("region", { name: "My followers since launch" }).getByRole("img")).toBeVisible();
    await expect(page.getByRole("region", { name: "My recent experiments" })).toContainText(
      "No experiments yet — the first one starts once I post daily.",
    );
    await expect(page.getByRole("region", { name: "Weekly notes" })).toContainText("No weekly notes yet.");
    await page.getByRole("link", { name: "Experiments", exact: true }).click();
    await expect(page.getByRole("heading", { level: 1, name: "No experiments yet" })).toBeVisible();
    await expect(page.getByRole("region", { name: /Effects at a glance/ })).toHaveCount(0);
  });

  test("empty database: no followers, no scores, nothing to chart", async ({ page, request }) => {
    const j = await journal(request, EMPTY);
    expect(j.followers).toEqual([]);
    await page.goto(`${EMPTY}/journal/#/`);
    await expect(page.getByRole("heading", { level: 1, name: j.site.headline })).toBeVisible();
    await expect(page.getByText("No follower counts yet: the first daily run sets the starting point.")).toBeVisible();
    await expect(page.getByRole("group", { name: "Totals so far" })).toContainText("—followers");
    await expect(page.getByText("not updated yet")).toBeVisible();
    await page.goto(`${EMPTY}/journal/#/method`);
    for (const meter of await page.getByRole("meter").all()) await expect(meter).toHaveAttribute("aria-valuenow", "0");
    await expect(page.getByText("not enough posts yet").first()).toBeVisible();
  });
});
