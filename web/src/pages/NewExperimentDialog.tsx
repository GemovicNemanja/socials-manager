import { useState } from "react";
import { useProposeExperiment } from "../api/hooks";
import type { CustomSpec, ExperimentAction } from "../api/models";
import { Button } from "../components/Button";
import { DialogFrame, Field, Notice, SelectField } from "../components/Dialog";

// Per-post levers from the API's CustomSpecIn schema; `satisfies` keeps them in step with the types.
const LEVERS = [
  ["hook", "Hook (opening line)"],
  ["format", "Format"],
  ["media", "Media"],
  ["link_placement", "Link placement"],
  ["cta", "Call to action"],
  ["content_kind", "Content kind"],
  ["topic_ordering", "Topic order"],
  ["quote", "Quote vs original"],
  ["thread_vs_long", "Thread vs long post"],
] as const satisfies readonly (readonly [CustomSpec["lever"], string])[];

const inputClass =
  "h-8 rounded-sm border border-rule bg-ground px-2.5 text-[13px] leading-normal text-text outline-none placeholder:text-faint focus:border-accent";

/** A one-factor per-post experiment written by the owner. Over 8 weeks it waits for an approval. */
export function NewExperimentDialog({ onClose, onDone }: { onClose: () => void; onDone: (result: ExperimentAction) => void }) {
  const [name, setName] = useState("");
  const [hypothesis, setHypothesis] = useState("");
  const [lever, setLever] = useState<CustomSpec["lever"]>("hook");
  const [control, setControl] = useState("");
  const [treatment, setTreatment] = useState("");
  const [weeks, setWeeks] = useState("4");
  const propose = useProposeExperiment();
  const refused = propose.data && !propose.data.done ? propose.data : null;
  const horizon = Number(weeks);
  const valid = name.trim() && hypothesis.trim() && control.trim() && treatment.trim() && horizon > 0 && horizon <= 52;

  const submit = () => {
    if (!valid) return;
    const custom: CustomSpec = {
      name: name.trim(),
      hypothesis: hypothesis.trim(),
      lever,
      control: control.trim(),
      treatment: treatment.trim(),
      horizon_weeks: horizon,
    };
    propose.mutate(
      { custom, suggestion: null },
      {
        onSuccess: (result) => {
          if (result.done) {
            onDone(result);
            onClose();
          }
        },
      },
    );
  };

  return (
    <DialogFrame
      title="New experiment"
      subtitle="One per-post factor, drawn 50/50 within each day. Up to 8 weeks starts directly; longer horizons and a 4th concurrent experiment wait for your approval."
      onClose={onClose}
      footer={
        <>
          <Button label="Cancel" showIcon={false} variant="ghost" onClick={onClose} />
          <Button
            label={propose.isPending ? "Starting…" : "Start experiment"}
            showIcon={false}
            variant="primary"
            disabled={!valid || propose.isPending}
            onClick={submit}
          />
        </>
      }
    >
      <Field label="Name">
        {(id) => <input id={id} value={name} onChange={(e) => setName(e.target.value)} maxLength={120} className={inputClass} />}
      </Field>
      <Field label="Hypothesis">
        {(id) => (
          <textarea
            id={id}
            value={hypothesis}
            onChange={(e) => setHypothesis(e.target.value)}
            maxLength={1000}
            className="min-h-[64px] resize-y rounded-sm border border-rule bg-ground px-2.5 py-2 text-[13px] leading-normal text-text outline-none focus:border-accent"
          />
        )}
      </Field>
      <div className="flex gap-3">
        <SelectField label="Lever" value={lever} onChange={(v) => setLever(v as CustomSpec["lever"])}>
          {LEVERS.map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </SelectField>
        <div className="w-[120px]">
          <Field label="Weeks">
            {(id) => (
              <input id={id} type="number" min={1} max={52} value={weeks} onChange={(e) => setWeeks(e.target.value)} className={inputClass} />
            )}
          </Field>
        </div>
      </div>
      <div className="flex gap-3">
        <div className="flex-1">
          <Field label="Control arm">
            {(id) => <input id={id} value={control} onChange={(e) => setControl(e.target.value)} placeholder="standard" className={`${inputClass} w-full`} />}
          </Field>
        </div>
        <div className="flex-1">
          <Field label="Treatment arm">
            {(id) => (
              <input id={id} value={treatment} onChange={(e) => setTreatment(e.target.value)} placeholder="outcome_first" className={`${inputClass} w-full`} />
            )}
          </Field>
        </div>
      </div>
      {refused && <Notice tone="negative">{refused.summary}</Notice>}
      {propose.isError && <Notice tone="negative">Couldn’t start: {propose.error.message}</Notice>}
    </DialogFrame>
  );
}
