import type { ExperimentDetail, FactorPast, FactorReadout, ReadoutView, SlotRow } from "../api/models";
import { Badge, type BadgeTone } from "../components/Badge";
import { ColumnHeader } from "../components/ColumnHeader";
import { KeyValue } from "../components/KeyValue";
import { LegendItem } from "../components/LegendItem";
import { Meter } from "../components/Meter";
import { PanelHeader } from "../components/PanelHeader";
import { SectionLabel } from "../components/SectionLabel";
import { StatusPill } from "../components/StatusPill";
import { cx } from "../lib/cx";
import { SLOT_OUTCOME, armsSummary, dayChip, formatProb, formatRatio, formatSlotRange, sentence, slotClock } from "../lib/experiments";
import { EMPTY, formatDay, formatInt, formatLabel } from "../lib/format";

const HEADLINE_TONE: Record<FactorReadout["decision"], BadgeTone> = {
  adopt: "positive",
  reject: "negative",
  near_threshold: "warn",
  continue: "default",
};
/** Scored and under-48h slots shown in the past strip (newest first). */
const PAST_ROWS = 10;

/** The selected experiment: protocol, per-factor readout cards (MODEL §6.3), how its posts landed
 * against their frozen treatment-blind forecasts, guardrails, health checks and upcoming slots. */
export function ExperimentReadout({ detail, now }: { detail: ExperimentDetail; now: number }) {
  const exp = detail.experiment!;
  const tz = detail.tz;
  const readout = detail.readout;
  const week = exp.week != null ? `week ${exp.week} of ${exp.weeks_total}` : `${exp.weeks_total} weeks`;
  const next = exp.next_look ? `next look at the first run after ${formatDay(exp.next_look.after, tz)}` : null;
  const upcoming = detail.slots.filter((s) => s.state === "planned" && Date.parse(s.planned_at) > now - 30 * 60_000);
  return (
    <section aria-label={`${exp.key} readout`} className="flex flex-col border-t border-rule">
      <PanelHeader
        title={`${exp.key} readout`}
        subtitle={[exp.name, week, readout ? `last look ${readout.look}` : null, next].filter(Boolean).join(" · ")}
        showAction={false}
      />
      <div className="flex gap-6 px-4 pb-4">
        <div className="flex w-[380px] shrink-0 flex-col">
          <Protocol detail={detail} />
        </div>
        <div className="flex min-w-0 flex-1 flex-col gap-3">
          {readout ? (
            readout.factors.map((f) => <FactorCard key={f.factor} factor={f} readouts={detail.readouts} delta={exp.delta_ratio} />)
          ) : (
            <div className="rounded-sm border border-dashed border-rule p-3 text-xs leading-[1.45] text-faint">
              No readout yet. The decision rule is read only at the first run after each experiment-week boundary
              {exp.next_look ? `; the first look is at the first run after ${formatDay(exp.next_look.after, tz)}` : ""}.
            </div>
          )}
        </div>
      </div>

      <PastAgainstForecasts past={detail.past} slots={detail.slots} experiment={exp.key} tz={tz} />
      <Guardrails detail={detail} readout={readout} />
      {upcoming.length > 0 && <UpcomingSlots slots={upcoming} />}
    </section>
  );
}

function Protocol({ detail }: { detail: ExperimentDetail }) {
  const exp = detail.experiment!;
  const perPost = exp.design === "per_post";
  const factors = exp.factors.map((f) => `${f.lever_label}: ${formatLabel(f.treatment)} vs ${formatLabel(f.control)}`).join("; ");
  const latest = detail.protocol_versions.at(-1);
  return (
    <>
      <KeyValue label="Hypothesis" value={exp.hypothesis} className="w-full" />
      <KeyValue label="Factors" value={factors} className="w-full" />
      <KeyValue label="Unit" value={perPost ? sentence(exp.factors[0]?.applies_to ?? "posts") : "Measured days"} className="w-full" />
      <KeyValue
        label="Assignment"
        value={perPost ? "Coin flip per post, balanced by day" : "4-day blocks: 2 washout, 2 measured"}
        className="w-full"
      />
      <KeyValue label="Primary metric" value={sentence(exp.primary_metric_label)} className="w-full" />
      <KeyValue label="Adjustment" value="Log blind forecast as covariate, not offset" className="w-full" />
      <KeyValue label="Decision rule" value={`Adopt if P(> ${exp.delta_ratio}×) ≥ 0.80, loss < 0.01, weekly`} className="w-full" />
      <KeyValue label="Guardrail" value="Pause arm if harm bound < 0.8×" className="w-full" />
      <KeyValue
        label="Protocol"
        value={latest ? `v${latest.version} · ${latest.hash.slice(0, 10)} · seed ${detail.seed ?? EMPTY}` : EMPTY}
        mono
        className="w-full"
      />
    </>
  );
}

function FactorCard({ factor: f, readouts, delta }: { factor: FactorReadout; readouts: ReadoutView[]; delta: number }) {
  const effect =
    f.effect_ratio != null && f.interval80
      ? `${formatRatio(f.effect_ratio)} [${f.interval80[0]!.toFixed(2)}, ${f.interval80[1]!.toFixed(2)}]`
      : "too early";
  return (
    <section aria-label={`Factor ${f.factor}`} className="flex flex-col gap-2 rounded-sm border border-rule bg-raised p-3">
      <div className="flex items-center gap-2">
        <span className="text-[13px] leading-normal font-medium text-text">
          {formatLabel(f.treatment)} <span className="text-faint">÷</span> {formatLabel(f.control)}
        </span>
        <span className="font-mono text-[11px] text-faint">{f.factor}</span>
        <Badge label={f.headline} tone={HEADLINE_TONE[f.decision]} className="ml-auto h-[18px] w-fit" />
      </div>
      <div className="flex flex-col">
        <span className="font-mono text-base leading-normal whitespace-nowrap text-text">{effect}</span>
        <span className="text-[11px] leading-normal text-faint">
          effect, 80% interval · {f.n_treated} treated vs {f.n_control} control posts matured
        </span>
      </div>
      {readouts.length > 0 && f.effect_ratio != null && <EstimateChart readouts={readouts} factor={f.factor} delta={delta} />}
      <Meter
        label={`P(effect > ${delta}×)`}
        value={`${formatProb(f.p_gt_delta)} · adopt at 0.80`}
        fraction={f.p_gt_delta ?? 0}
        fill={f.p_gt_delta != null && f.p_gt_delta >= 0.8 ? "positive" : "accent"}
        className="w-full"
      />
      <div className="flex flex-col">
        <KeyValue label="P(effect < 0)" value={`${formatProb(f.p_lt_zero)} · reject at 0.90`} mono className="w-full" />
        <KeyValue label="Expected loss" value={`${formatProb(f.expected_loss, 3)} · adopt below 0.01`} mono className="w-full" />
        <KeyValue label="Randomization p" value={`${formatProb(f.randomization_p, 3)} · 9,999 re-draws`} mono className="w-full" />
        <KeyValue label="e-LOND" value={<span title={f.elond}>{f.elond}</span>} className="w-full" />
      </div>
      <p className="text-[11px] leading-[1.45] text-muted">
        {sentence(f.reason)}.
        {f.disagrees_with_randomization_test && (
          <span className="text-warn"> The posterior and the randomization test disagree: review before acting.</span>
        )}
      </p>
    </section>
  );
}

/** The factor's estimate and 80% interval at each look, on a log scale with 1× and δ marked. */
function EstimateChart({ readouts, factor, delta }: { readouts: ReadoutView[]; factor: string; delta: number }) {
  const points = readouts
    .map((r) => ({ look: r.look, f: r.factors.find((x) => x.factor === factor) }))
    .filter((p): p is { look: string; f: FactorReadout } => p.f?.effect_ratio != null && p.f.interval80 != null);
  if (points.length === 0) return null;
  const lo = Math.log(0.5);
  const hi = Math.log(2);
  const y = (ratio: number) => 4 + (1 - (Math.log(Math.min(2, Math.max(0.5, ratio))) - lo) / (hi - lo)) * 92;
  const x = (i: number) => (points.length === 1 ? 200 : 16 + (i / (points.length - 1)) * 368);
  const band = [
    ...points.map((p, i) => `${x(i)},${y(p.f.interval80![1]!)}`),
    ...points.map((p, i) => `${x(i)},${y(p.f.interval80![0]!)}`).reverse(),
  ].join(" ");
  const line = points.map((p, i) => `${x(i)},${y(p.f.effect_ratio!)}`).join(" ");
  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-center gap-4">
        <span className="flex-1 text-[11px] leading-normal text-muted">Estimate at each look</span>
        <LegendItem label="Estimate" swatch="accent" />
        <LegendItem label="80% interval" swatch="band" />
        <LegendItem label={`δ = ${delta}×`} swatch="faint" />
      </div>
      <div className="flex gap-2">
        <div className="flex w-7 flex-col justify-between text-right font-mono text-[10px] leading-normal text-faint">
          <span>2×</span>
          <span>1×</span>
          <span>0.5×</span>
        </div>
        <svg role="img" aria-label="Estimate at each look" viewBox="0 0 400 100" preserveAspectRatio="none" className="h-[100px] flex-1 border border-grid bg-plot">
          <line x1="0" x2="400" y1={y(1)} y2={y(1)} stroke="var(--dashboard-faint)" strokeDasharray="3 3" vectorEffect="non-scaling-stroke" />
          <line x1="0" x2="400" y1={y(delta)} y2={y(delta)} stroke="var(--dashboard-rule-strong)" vectorEffect="non-scaling-stroke" />
          {points.length > 1 ? (
            <>
              <polygon points={band} fill="var(--dashboard-band)" fillOpacity="0.85" />
              <polyline points={line} fill="none" stroke="var(--dashboard-accent)" strokeWidth="2" vectorEffect="non-scaling-stroke" />
            </>
          ) : (
            <line x1="200" x2="200" y1={y(points[0]!.f.interval80![1]!)} y2={y(points[0]!.f.interval80![0]!)} stroke="var(--dashboard-band)" strokeWidth="6" />
          )}
          {points.map((p, i) => (
            <circle key={p.look} cx={x(i)} cy={y(p.f.effect_ratio!)} r="2.5" fill="var(--dashboard-accent)" />
          ))}
        </svg>
      </div>
      <div className="flex justify-between pl-9 font-mono text-[10px] leading-normal text-faint">
        {points.map((p) => (
          <span key={p.look}>{p.look}</span>
        ))}
      </div>
    </div>
  );
}

/** Plan §6: every expectation shows how the past landed. Here: each arm's posts against their
 * frozen treatment-blind forecasts (above / as expected / below, MODEL §4.1). */
function PastAgainstForecasts({ past, slots, experiment, tz }: { past: FactorPast[]; slots: SlotRow[]; experiment: string; tz: string }) {
  const shown = slots
    .filter((s) => s.state === "posted" || s.state === "missed")
    .slice(-PAST_ROWS)
    .reverse();
  return (
    <section aria-label="Posts against blind forecasts" className="flex flex-col border-t border-rule px-4 pb-4">
      <PanelHeader
        title="How its posts landed"
        subtitle="Each post against its frozen treatment-blind forecast · intention to treat"
        showAction={false}
        inset={false}
      />
      <div role="row" className="flex h-7 items-center border-y border-rule bg-raised">
        <ColumnHeader label="Arm" className="flex-1" />
        <ColumnHeader label="Above" align="right" className="w-[84px]" />
        <ColumnHeader label="As expected" align="right" className="w-[84px]" />
        <ColumnHeader label="Below" align="right" className="w-[84px]" />
        <ColumnHeader label="Under 48h" align="right" className="w-[84px]" />
        <ColumnHeader label="Missed" align="right" className="w-[84px]" />
      </div>
      {past.map((p) => (
        <div key={p.factor} className="flex flex-col">
          <ArmCounts label={`Treated · ${formatLabel(p.treatment)}`} factor={p.factor} counts={p.treated} />
          <ArmCounts label={`Control · ${formatLabel(p.control)}`} factor={p.factor} counts={p.controls} />
        </div>
      ))}
      <SectionLabel label={`Latest posts · time (${tz}) · arm · blind 80% range · 48h impressions`} className="w-full px-0 pt-3" />
      {shown.length === 0 ? (
        <p className="py-1 text-xs leading-[1.45] text-faint">No assigned post has been published yet.</p>
      ) : (
        shown.map((s) => {
          const own = s.arms.filter((a) => a.experiment === experiment);
          const o = SLOT_OUTCOME[s.outcome_status];
          return (
            <div key={s.slot} className="flex h-[26px] items-center gap-3 border-b border-grid text-xs leading-normal">
              <span className="w-[96px] shrink-0 font-mono text-[11px] text-faint">
                {dayChip(s.planned_local.slice(0, 10))} {slotClock(s)}
              </span>
              <span className="min-w-0 flex-1 truncate text-muted">
                {own.map((a) => `${a.factor}: ${formatLabel(a.value)}`).join(" · ")}
              </span>
              <span className="w-[96px] shrink-0 text-right font-mono text-faint">{formatSlotRange(s)}</span>
              <span className="w-[64px] shrink-0 text-right font-mono text-text">{formatInt(s.outcome_i48)}</span>
              <StatusPill label={o.label} status={o.pill} className="h-5 w-[96px] shrink-0 pl-3" />
            </div>
          );
        })
      )}
    </section>
  );
}

function ArmCounts({ label, factor, counts }: { label: string; factor: string; counts: FactorPast["treated"] }) {
  const cells = [
    [counts.above, "text-positive"],
    [counts.as_expected, "text-muted"],
    [counts.below, "text-negative"],
    [counts.awaiting, "text-faint"],
    [counts.lost, counts.lost ? "text-warn" : "text-faint"],
  ] as const;
  return (
    <div role="row" aria-label={label} className="flex h-[26px] items-center border-b border-grid">
      <span className="min-w-0 flex-1 truncate text-xs leading-normal text-text">
        {label} <span className="font-mono text-[11px] text-faint">{factor}</span>
      </span>
      {cells.map(([n, tone], i) => (
        <span key={i} data-cell className={cx("w-[84px] text-right font-mono text-xs leading-normal", tone)}>
          {n}
        </span>
      ))}
    </div>
  );
}

function Guardrails({ detail, readout }: { detail: ExperimentDetail; readout: ReadoutView | null }) {
  const exp = detail.experiment!;
  const paused = exp.paused_arms;
  return (
    <section aria-label="Guardrails and health" className="flex gap-6 border-t border-rule px-4 pb-4">
      <div className="flex w-[380px] shrink-0 flex-col">
        <PanelHeader title="Guardrails" subtitle="Arm harm and unfollows, always valid" showAction={false} inset={false} />
        {paused.length === 0 && !exp.pause_reason ? (
          <KeyValue label="Arm harm" value="No arm paused" className="w-full" />
        ) : (
          <>
            {paused.map((p) => (
              <KeyValue key={p.factor} label={`Paused: ${p.factor}`} value={p.detail} className="w-full" />
            ))}
            {exp.pause_reason && <KeyValue label="Experiment paused" value={exp.pause_reason} className="w-full" />}
          </>
        )}
        <SectionLabel label={`Deviations · ${detail.deviations.length}`} className="w-full px-0 pt-3" />
        {detail.deviations.length === 0 ? (
          <span className="text-xs leading-normal text-faint">None logged.</span>
        ) : (
          detail.deviations.slice(-5).map((d, i) => (
            <KeyValue key={i} label={`${formatLabel(d.kind)}`} value={`${d.slot ?? ""} ${d.factor} · ${d.detail}`} className="w-full" />
          ))
        )}
      </div>
      <div className="flex min-w-0 flex-1 flex-col">
        <PanelHeader
          title="Health checks"
          subtitle={readout ? `at the ${readout.look} look` : "run with each readout"}
          showAction={false}
          inset={false}
        />
        {readout ? (
          readout.health.map((h) => (
            <div key={h.name} className="flex h-[26px] items-center gap-2 border-b border-grid">
              <span className={cx("size-1.5 shrink-0 rounded-full", h.ok ? "bg-positive" : "bg-warn")} />
              <span className="w-[200px] shrink-0 text-xs leading-normal text-text">{h.name}</span>
              <span className="min-w-0 flex-1 truncate text-[11px] leading-normal text-faint" title={h.detail}>
                {h.detail}
              </span>
            </div>
          ))
        ) : (
          <span className="text-xs leading-normal text-faint">SRM, maturity, attrition by arm, blind-forecast timing and seed checks run with the first readout.</span>
        )}
      </div>
    </section>
  );
}

function UpcomingSlots({ slots }: { slots: SlotRow[] }) {
  return (
    <section aria-label="Upcoming slots" className="flex flex-col border-t border-rule px-4 pb-4">
      <PanelHeader title="Upcoming slots" subtitle="Arms drawn at registration, after the blind forecast froze" showAction={false} inset={false} />
      {slots.map((s) => (
        <div key={s.slot} className="flex items-start gap-3 border-b border-grid py-1.5">
          <span className="w-[86px] shrink-0 font-mono text-[11px] leading-[1.45] text-faint">
            {dayChip(s.planned_local.slice(0, 10))} {slotClock(s)}
          </span>
          <span className="w-[160px] shrink-0 text-xs leading-[1.45] text-muted">{armsSummary(s.arms)}</span>
          <span className="min-w-0 flex-1 text-xs leading-[1.45] text-text">{sentence(s.arms.map((a) => a.instruction).join("; "))}</span>
          <span className="w-[96px] shrink-0 text-right font-mono text-[11px] leading-[1.45] text-faint">{formatSlotRange(s)}</span>
        </div>
      ))}
    </section>
  );
}
