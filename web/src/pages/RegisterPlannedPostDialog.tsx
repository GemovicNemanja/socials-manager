import { type ReactNode, useEffect, useId, useMemo, useRef, useState } from "react";
import { ApiError } from "../api/client";
import { useDraftForecast, useRegisterPlanned } from "../api/hooks";
import type {
  DraftResponse,
  GuardrailHit,
  PlannedFormat,
  PlannedPostType,
  PlannedTopic,
  PlanRequest,
  RegisterResponse,
} from "../api/models";
import { Button } from "../components/Button";
import { icons } from "../components/icons";
import { Toggle } from "../components/Toggle";
import { cx } from "../lib/cx";
import { formatInt, formatLabel } from "../lib/format";
import { defaultPlannedAt, utcToZonedInput, zoneAbbreviation, zonedInputToUtc } from "../lib/time";
import { useNow } from "../lib/useNow";

const CloseIcon = icons.ActionClose;
const CalendarIcon = icons.ActionCalendar;
const ChevronIcon = icons.ActionExpand;
const DRAFT_DEBOUNCE_MS = 400;

// Choices from the API's PlanIn schema; `satisfies` keeps them in step with the generated types.
const POST_TYPES = [
  ["original", "Original"],
  ["quote", "Quote"],
  ["reply", "Reply"],
  ["self_reply", "Thread reply"],
] as const satisfies readonly (readonly [PlannedPostType, string])[];
const FORMATS = ["Text", "Image", "Video", "Text + link", "Thread", "Poll"] as const satisfies readonly PlannedFormat[];
const TOPICS = [
  "ai",
  "software_development",
  "science_technology",
  "career",
  "personal",
  "other",
] as const satisfies readonly PlannedTopic[];

function useDebounced<T>(value: T, ms: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const id = window.setTimeout(() => setDebounced(value), ms);
    return () => window.clearTimeout(id);
  }, [value, ms]);
  return debounced;
}

export interface RegisterPlannedPostDialogProps {
  tz: string;
  onClose: () => void;
  onRegistered?: (result: RegisterResponse) => void;
}

/** Register a planned post: a live draft range and guardrails, then Register freezes the forecast. */
export function RegisterPlannedPostDialog({ tz, onClose, onRegistered }: RegisterPlannedPostDialogProps) {
  const titleId = useId();
  const [when, setWhen] = useState(() => utcToZonedInput(defaultPlannedAt(), tz));
  const [text, setText] = useState("");
  const [postType, setPostType] = useState<PlannedPostType>("original");
  const [format, setFormat] = useState<PlannedFormat | "">("");
  const [topic, setTopic] = useState<PlannedTopic | "">("");
  const [hasLink, setHasLink] = useState(false);
  const firstField = useRef<HTMLInputElement>(null);
  const register = useRegisterPlanned();

  const now = useNow(15_000);
  const plannedIso = zonedInputToUtc(when, tz)?.toISOString() ?? null;
  const inPast = plannedIso != null && Date.parse(plannedIso) <= now;
  const request = useMemo<PlanRequest | null>(
    () =>
      plannedIso == null || inPast
        ? null
        : {
            planned_at: plannedIso,
            text: text.trim() || null,
            post_type: postType,
            post_format: format || null,
            topic: topic || null,
            has_link: hasLink,
          },
    [plannedIso, inPast, text, postType, format, topic, hasLink],
  );
  const draft = useDraftForecast(useDebounced(request, DRAFT_DEBOUNCE_MS));
  const hits = draft.data?.guardrails ?? [];
  const blocked = hits.some((h) => h.severity === "block");
  const warned = hits.some((h) => h.severity === "warn");
  const refused = register.data && !register.data.registered ? register.data : null;

  useEffect(() => {
    firstField.current?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  const submit = () => {
    if (!request || blocked) return;
    register.mutate(
      { ...request, accept_warnings: warned },
      {
        onSuccess: (result) => {
          if (result.registered) {
            onRegistered?.(result);
            onClose();
          }
        },
      },
    );
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-ground/70 p-6" onMouseDown={onClose}>
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        onMouseDown={(e) => e.stopPropagation()}
        className="flex max-h-full w-[560px] flex-col overflow-hidden rounded-lg border border-rule-strong bg-raised font-sans"
      >
        <div className="flex items-start gap-3 border-b border-rule px-5 pt-5 pb-4">
          <div className="flex flex-1 flex-col gap-1">
            <h2 id={titleId} className="text-base leading-normal font-semibold text-text">
              Register a planned post
            </h2>
            <p className="text-xs leading-[1.45] text-muted">
              Its forecast is frozen now, before you post, so the comparison stays honest.
            </p>
          </div>
          <button type="button" aria-label="Close" onClick={onClose} className="mt-0.5 size-4 shrink-0 text-faint hover:text-muted">
            <CloseIcon aria-hidden className="size-4" />
          </button>
        </div>

        <div className="flex flex-col gap-4 overflow-y-auto px-5 py-4">
          <Field label="Planned time">
            {(id) => (
              <div className="flex h-8 items-center gap-2 rounded-sm border border-rule bg-ground px-2.5 focus-within:border-accent">
                <button
                  type="button"
                  aria-label="Open the date picker"
                  onClick={() => firstField.current?.showPicker?.()}
                  className="shrink-0 text-faint hover:text-muted"
                >
                  <CalendarIcon aria-hidden className="size-3.5" />
                </button>
                <input
                  id={id}
                  ref={firstField}
                  type="datetime-local"
                  value={when}
                  onChange={(e) => setWhen(e.target.value)}
                  className="min-w-0 flex-1 bg-transparent font-mono text-xs leading-[1.33333] text-text outline-none [color-scheme:dark] [&::-webkit-calendar-picker-indicator]:hidden"
                />
                <span className="font-mono text-xs leading-[1.33333] text-faint">
                  {zoneAbbreviation(new Date(plannedIso ?? now), tz)}
                </span>
              </div>
            )}
          </Field>
          {inPast && <Notice tone="negative">Pick a time in the future: a live forecast is frozen before the post exists.</Notice>}

          <Field label="Draft text (optional)">
            {(id) => (
              <textarea
                id={id}
                value={text}
                onChange={(e) => setText(e.target.value)}
                maxLength={4000}
                placeholder="What you plan to post. With text, the next run matches the post by its wording."
                className="min-h-[88px] resize-y rounded-sm border border-rule bg-ground px-2.5 py-2 text-[13px] leading-normal text-text outline-none placeholder:text-faint focus:border-accent"
              />
            )}
          </Field>

          <div className="flex gap-3">
            <Select label="Type" value={postType} onChange={(v) => setPostType(v as PlannedPostType)}>
              {POST_TYPES.map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </Select>
            <Select label="Format" value={format} onChange={(v) => setFormat(v as PlannedFormat | "")}>
              <option value="">From the text</option>
              {FORMATS.map((f) => (
                <option key={f} value={f}>
                  {f}
                </option>
              ))}
            </Select>
            <Select label="Topic" value={topic} onChange={(v) => setTopic(v as PlannedTopic | "")}>
              <option value="">Not set</option>
              {TOPICS.map((t) => (
                <option key={t} value={t}>
                  {formatLabel(t)}
                </option>
              ))}
            </Select>
          </div>

          <Toggle label="Contains a link" checked={hasLink} onChange={setHasLink} />

          <ForecastPreview draft={draft.data} pending={draft.isFetching} error={draft.error} idle={request == null} />

          {hits.map((hit) => (
            <GuardrailNotice key={`${hit.rule}-${hit.ref ?? ""}`} hit={hit} />
          ))}
          {refused && <Notice tone="negative">{refused.summary}</Notice>}
          {register.isError && <Notice tone="negative">Couldn’t register: {register.error.message}</Notice>}
        </div>

        <div className="flex items-center justify-end gap-2 border-t border-rule px-5 py-3.5">
          <Button label="Cancel" showIcon={false} variant="ghost" onClick={onClose} />
          <Button
            label={register.isPending ? "Registering…" : warned ? "Register anyway and freeze" : "Register and freeze forecast"}
            showIcon={false}
            variant="primary"
            disabled={!request || blocked || register.isPending}
            onClick={submit}
          />
        </div>
      </div>
    </div>
  );
}

function Field({ label, children }: { label: string; children: (id: string) => ReactNode }) {
  const id = useId();
  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={id} className="text-xs leading-[1.33333] text-muted">
        {label}
      </label>
      {children(id)}
    </div>
  );
}

function Select({
  label,
  value,
  onChange,
  children,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  children: ReactNode;
}) {
  return (
    <div className="min-w-0 flex-1">
      <Field label={label}>
        {(id) => (
          <div className="relative flex h-8 items-center rounded-sm border border-rule bg-ground focus-within:border-accent">
            <select
              id={id}
              value={value}
              onChange={(e) => onChange(e.target.value)}
              className="h-full w-full appearance-none bg-transparent pr-7 pl-2.5 text-[13px] leading-normal text-text outline-none [color-scheme:dark]"
            >
              {children}
            </select>
            <ChevronIcon aria-hidden className="pointer-events-none absolute right-2.5 size-3.5 text-faint" />
          </div>
        )}
      </Field>
    </div>
  );
}

function ForecastPreview({
  draft,
  pending,
  error,
  idle,
}: {
  draft: DraftResponse | undefined;
  pending: boolean;
  error: Error | null;
  idle: boolean;
}) {
  const q = draft?.forecast?.quantiles;
  const noRunYet = error instanceof ApiError && error.status === 409;
  const kind =
    draft?.data_sufficiency.reason === "prior_only_uncalibrated" ? "prior only · uncalibrated" : (draft?.confidence ?? "");
  // Band on a linear scale that puts the 90% quantile three quarters along.
  const max = q ? Math.max(1, (q.q90 ?? 0) / 0.75) : 1;
  const at = (v: number | undefined) => `${Math.min(100, Math.max(0, ((v ?? 0) / max) * 100))}%`;
  return (
    <section aria-label="Draft forecast" aria-busy={pending} className="flex flex-col gap-2">
      <div className="flex items-center gap-2">
        <span className="flex-1 text-xs leading-[1.33333] text-muted">Expected impressions at 48h</span>
        <span className="font-mono text-[11px] leading-normal text-warn">{pending ? "updating…" : kind}</span>
      </div>
      <div className="relative h-2 w-full rounded-full bg-surface">
        {q && !error && (
          <>
            <div
              className="absolute h-full rounded-full bg-band"
              style={{ left: at(q.q10), width: `calc(${at(q.q90)} - ${at(q.q10)})` }}
            />
            <div className="absolute h-full w-0.5 bg-accent" style={{ left: at(q.q50) }} />
          </>
        )}
      </div>
      <div className="flex justify-between font-mono text-xs leading-[1.33333]">
        {noRunYet ? (
          <span className="text-faint">A draft range needs one finished run; registering still freezes a prior-only forecast.</span>
        ) : error ? (
          <span className="text-negative">Couldn’t get a draft range ({error.message}).</span>
        ) : q ? (
          <>
            <span className="text-faint">
              80% range {formatInt(q.q10)} – {formatInt(q.q90)}
            </span>
            <span className="text-text">median {formatInt(q.q50)}</span>
          </>
        ) : (
          <span className="text-faint">{idle ? "Pick a future time to see the range." : "Loading the draft range…"}</span>
        )}
      </div>
    </section>
  );
}

function GuardrailNotice({ hit }: { hit: GuardrailHit }) {
  return (
    <Notice tone={hit.severity === "block" ? "negative" : "warn"}>
      {hit.detail}
      {hit.severity === "block" ? " This can’t be registered." : " You can register anyway."}
    </Notice>
  );
}

function Notice({ tone, children }: { tone: "warn" | "negative"; children: ReactNode }) {
  return (
    <div
      role={tone === "negative" ? "alert" : "status"}
      className={cx("flex items-start gap-2 rounded-sm p-2.5", tone === "warn" ? "bg-warn-subtle" : "bg-negative-subtle")}
    >
      <span className={cx("mt-1.5 block size-1.5 shrink-0 rounded-full", tone === "warn" ? "bg-warn" : "bg-negative")} />
      <span className="text-xs leading-[1.45] text-text">{children}</span>
    </div>
  );
}
