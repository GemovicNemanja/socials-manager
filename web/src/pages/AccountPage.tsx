import { useState } from "react";
import { useNextPosts, useOverview, useShell } from "../api/hooks";
import type { SlotRow } from "../api/models";
import { Button } from "../components/Button";
import { PanelHeader } from "../components/PanelHeader";
import { SectionLabel } from "../components/SectionLabel";
import { armsSummary, formatSlotRange, relativeDay, sentence, slotClock } from "../lib/experiments";
import { formatInt } from "../lib/format";
import { useNow } from "../lib/useNow";
import { RegisterPlannedPostDialog } from "./RegisterPlannedPostDialog";
import { RegisterSlotsDialog } from "./RegisterSlotsDialog";

/** Panels the canvas shows that need the M3 follower model; labelled so instead of faked. */
const M3_PANELS = [
  {
    title: "Followers vs forecast",
    body: "The follower forecast with its 80% band, frozen at each run and later scored against what happened, so past growth reads against what was expected.",
  },
  {
    title: "Follower funnel, 7 days",
    body: "Impressions → profile visits → follows → unfollows, each step against its forecast.",
  },
  { title: "Goal", body: "Goal probability from 4,000 simulated follower paths, with its history." },
  { title: "Posts that brought followers", body: "Follows per post from owner analytics, when the endpoint is available." },
];

/** Account (plan §1): "Next posts" with their experiment arms (M2); follower panels arrive in M3. */
export function AccountPage() {
  const shell = useShell();
  const overview = useOverview();
  const next = useNextPosts();
  const [dialog, setDialog] = useState<"slots" | "post" | null>(null);
  const tz = next.data?.tz ?? shell.data?.account?.tz ?? "UTC";
  const handle = shell.data?.account?.handle;
  const followers = overview.data?.tiles.followers.now;

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <header className="flex h-11 shrink-0 items-center gap-3 border-b border-rule px-4">
        <h1 className="text-sm leading-normal font-medium text-text">Account</h1>
        <span className="text-xs leading-normal whitespace-nowrap text-faint">
          {[handle, followers != null ? `${formatInt(followers)} followers` : null].filter(Boolean).join(" · ") || "No account yet"}
        </span>
        <div className="flex-1" />
        <Button label="Register post" icon="ActionCalendar" onClick={() => setDialog("post")} />
        <Button label="Register slots" icon="ActionAdd" variant="primary" disabled={!next.data} onClick={() => setDialog("slots")} />
      </header>

      <div className="flex min-h-0 flex-1">
        <div className="grid min-w-0 flex-1 auto-rows-min grid-cols-2 overflow-y-auto border-r border-rule">
          {M3_PANELS.map((p, i) => (
            <section
              key={p.title}
              aria-label={p.title}
              className={i % 2 === 0 ? "flex flex-col border-r border-b border-rule" : "flex flex-col border-b border-rule"}
            >
              <PanelHeader title={p.title} subtitle="arrives in M3" showAction={false} />
              <div className="mx-4 mb-4 flex h-[150px] flex-col justify-center gap-1.5 rounded-sm border border-dashed border-rule px-4">
                <span className="font-mono text-[11px] leading-normal tracking-[1px] text-faint uppercase">M3 · follower model</span>
                <span className="text-xs leading-[1.45] text-muted">{p.body}</span>
              </div>
            </section>
          ))}
        </div>

        <NextPosts slots={next.data?.slots ?? []} tz={tz} loading={next.isPending} drawing={next.data?.drawing ?? []} />
      </div>

      {dialog === "slots" && next.data && (
        <RegisterSlotsDialog
          tz={next.data.tz}
          drawing={next.data.drawing}
          registeredDays={next.data.registered_days}
          onClose={() => setDialog(null)}
        />
      )}
      {dialog === "post" && <RegisterPlannedPostDialog tz={tz} onClose={() => setDialog(null)} />}
    </div>
  );
}

function NextPosts({ slots, tz, loading, drawing }: { slots: SlotRow[]; tz: string; loading: boolean; drawing: string[] }) {
  const now = new Date(useNow(60_000));
  const days: { label: string; rows: SlotRow[] }[] = [];
  for (const s of slots) {
    const label = relativeDay(s.planned_at, tz, now);
    const last = days.at(-1);
    if (last && last.label === label) last.rows.push(s);
    else days.push({ label, rows: [s] });
  }
  return (
    <section aria-label="Next posts" className="flex w-[400px] shrink-0 flex-col overflow-y-auto">
      <PanelHeader title="Next posts" subtitle="next 7 days · arms drawn at registration" showAction={false} />
      <div className="flex flex-col px-4">
        {slots.length === 0 && (
          <p className="py-2 text-xs leading-[1.45] text-faint">
            {loading
              ? "Loading…"
              : drawing.length > 0
                ? `Nothing registered for the next 7 days. Register slots to draw arms for ${drawing.join(", ")}.`
                : "Nothing registered for the next 7 days. Start an experiment, then register slots; or register a planned post."}
          </p>
        )}
        {days.map((d) => (
          <div key={d.label} className="flex flex-col">
            <SectionLabel label={d.label} className="w-full px-0 pt-2" />
            {d.rows.map((s) => (
              <NextPostRow key={s.slot} slot={s} />
            ))}
          </div>
        ))}
      </div>
      <div className="mt-auto flex flex-col gap-1 border-t border-rule px-4 py-3">
        <span className="font-mono text-[11px] leading-normal tracking-[1px] text-faint uppercase">Agents</span>
        <span className="text-xs leading-[1.45] text-muted">
          Agents can register slots and start per-post experiments up to 4 weeks. Longer horizons, more than 3 experiments,
          amendments and exclusions wait for your approval on the Experiments screen.
        </span>
      </div>
    </section>
  );
}

function NextPostRow({ slot: s }: { slot: SlotRow }) {
  const what = s.arms.length > 0 ? sentence(s.arms.map((a) => a.instruction).join("; ")) : s.text || "Planned post";
  return (
    <div data-slot={s.slot} className="flex gap-3 border-b border-grid py-1.5">
      <span className="w-[44px] shrink-0 font-mono text-xs leading-[1.45] text-faint">{slotClock(s)}</span>
      <div className="flex min-w-0 flex-1 flex-col gap-0.5">
        <span className="truncate text-xs leading-[1.45] text-muted">
          {s.experiment_slot ? armsSummary(s.arms) : "no experiment"}
          {s.labels.content_kind ? ` · ${s.labels.content_kind}` : ""}
          {s.labels.link_post ? " · link post" : ""}
        </span>
        <span className="text-xs leading-[1.45] text-text">{what}</span>
        <span className="font-mono text-[11px] leading-normal text-faint">
          {s.forecast_kind === "treatment_blind" ? "blind" : "frozen"} 80% range {formatSlotRange(s)} · median {formatInt(s.forecast_q50)}
        </span>
      </div>
    </div>
  );
}
