import type { Journal } from "../data";
import { paths } from "../router";
import { Facts } from "./Layout";

/** The footer's three facts, read from the method sections (never the canvas's example claims). */
function footerFacts(journal: Journal): { label: string; value: string }[] {
  const facts = journal.method.sections.flatMap((s) => s.facts);
  const find = (label: string) => facts.find((f) => f.label === label)?.value;
  const rows = [
    { label: "Data", value: find("Source") },
    { label: "Forecasts", value: find("Frozen") && `Frozen ${find("Frozen")!.charAt(0).toLowerCase()}${find("Frozen")!.slice(1)}` },
    { label: "Scored on", value: find("Main outcome") },
    { label: "Method", value: find("Model") },
  ];
  return rows.filter((r): r is { label: string; value: string } => Boolean(r.value));
}

export function JournalFooter({ journal }: { journal: Journal }) {
  const rows = footerFacts(journal);
  if (!rows.length) return null;
  return (
    <footer className="flex flex-col gap-1.5 pt-2">
      <Facts rows={rows} label="About these numbers" />
      <a href={paths.method} className="w-fit pt-2 text-xs text-accent hover:underline">
        How the numbers are made →
      </a>
    </footer>
  );
}
