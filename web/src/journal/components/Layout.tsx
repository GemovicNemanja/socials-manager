import { useState, type ReactNode } from "react";
import { Badge } from "../../components/Badge";
import { Button, ButtonLink } from "../../components/Button";
import { KeyValue } from "../../components/KeyValue";
import { SectionLabel } from "../../components/SectionLabel";
import { Tab } from "../../components/Tab";
import { cx } from "../../lib/cx";
import type { Journal } from "../data";
import { updated } from "../format";
import { paths, type JournalRoute } from "../router";

export function TopBar({ journal, route }: { journal: Journal | null; route: JournalRoute }) {
  const section =
    route.page === "experiments" || route.page === "experiment"
      ? "experiments"
      : route.page === "method"
        ? "method"
        : route.page === "not-found"
          ? null
          : "journal";
  const site = journal?.site;
  return (
    <header className="flex min-h-14 w-full flex-wrap items-center gap-x-6 border-b border-rule px-5 sm:px-10">
      <a href={paths.home} className="flex items-center gap-2 py-2">
        <span className="text-sm leading-[1.42857] font-medium text-text">{site?.handle ?? "Journal"}</span>
        {site?.tagline && <span className="text-sm leading-[1.42857] text-faint">· {site.tagline}</span>}
      </a>
      <nav aria-label="Journal sections" className="flex h-14 items-center gap-5">
        <Tab label="Journal" showCount={false} href={paths.home} current={section === "journal" ? "active" : "default"} className="h-14 w-fit" />
        <Tab
          label="Experiments"
          count={String(journal?.experiments.length ?? 0)}
          showCount={(journal?.experiments.length ?? 0) > 0}
          href={paths.experiments}
          current={section === "experiments" ? "active" : "default"}
          className="h-14 w-fit"
        />
        <Tab label="Method" showCount={false} href={paths.method} current={section === "method" ? "active" : "default"} className="h-14 w-fit" />
      </nav>
      <div className="hidden flex-1 sm:block" />
      {journal?.preview && <Badge label="Preview — drafts shown" tone="warn" />}
      {site && <span className="font-mono text-[11px] leading-normal text-faint">{updated(site.updated_at)}</span>}
      {site && <ButtonLink label="Follow on X" variant="primary" href={site.x_url} target="_blank" rel="noopener noreferrer" />}
    </header>
  );
}

/** The page column: 760px wide on the canvas, full width with a margin on small screens. */
export function Column({ children, label }: { children: ReactNode; label?: string }) {
  return (
    <main aria-label={label} className="mx-auto flex w-full max-w-[800px] flex-col gap-10 px-5 pt-14 pb-20">
      {children}
    </main>
  );
}

export function Kicker({ children, tone = "accent" }: { children: ReactNode; tone?: "accent" | "faint" }) {
  return (
    <p
      className={cx(
        "font-mono text-[11px] leading-normal tracking-[1.2px] uppercase",
        tone === "accent" ? "text-accent" : "text-faint",
      )}
    >
      {children}
    </p>
  );
}

export function Title({ children }: { children: ReactNode }) {
  return <h1 className="text-[32px] leading-[1.2] font-medium tracking-[-0.4px] text-text">{children}</h1>;
}

export function Lede({ children }: { children: ReactNode }) {
  return <p className="text-[15px] leading-[1.6] text-muted">{children}</p>;
}

export function Stat({ value, label }: { value: string; label: string }) {
  return (
    <div className="flex flex-col gap-1">
      <span className="font-mono text-2xl leading-[1.33333] text-text">{value}</span>
      <span className="text-xs leading-[1.33333] text-faint">{label}</span>
    </div>
  );
}

export function Stats({ children, label, className }: { children: ReactNode; label: string; className?: string }) {
  return (
    <div role="group" aria-label={label} className={cx("flex flex-wrap gap-x-10 gap-y-4", className ?? "pt-4")}>
      {children}
    </div>
  );
}

/** A panel title row: "My followers since launch" with an optional note or legend on the right. */
export function PanelTitle({ title, children, id }: { title: string; children?: ReactNode; id?: string }) {
  return (
    <div className="flex min-h-8 flex-wrap items-center gap-x-4 gap-y-1">
      <h2 id={id} className="flex-1 text-[13px] leading-normal font-medium text-text">
        {title}
      </h2>
      {children}
    </div>
  );
}

export function Note({ children }: { children: ReactNode }) {
  return <span className="text-xs leading-[1.33333] text-faint">{children}</span>;
}

/** A section with a small uppercase heading (Weekly notes, Why I tested it ...). */
export function LabelledSection({
  label,
  children,
  className,
}: {
  label: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section aria-label={label} className={cx("flex flex-col", className)}>
      <SectionLabel as="h2" label={label} />
      {children}
    </section>
  );
}

/** What a section shows when there is nothing in it yet. */
export function EmptyState({ children }: { children: ReactNode }) {
  return (
    <p className="border-b border-grid px-3 py-4 text-[13px] leading-[1.6] text-faint" data-empty>
      {children}
    </p>
  );
}

export function BackLink({ href, label }: { href: string; label: string }) {
  return (
    <a href={href} className="w-fit text-xs leading-[1.33333] text-accent hover:underline">
      ← {label}
    </a>
  );
}

export function Facts({ rows, label }: { rows: { label: string; value: ReactNode }[]; label?: string }) {
  return (
    <div role="group" aria-label={label} className="flex flex-col">
      {rows.map((r) => (
        <KeyValue key={r.label} label={r.label} value={r.value} wrap className="w-full" />
      ))}
    </div>
  );
}

/** Follow on X and Copy link, at the end of a note or an experiment. */
export function ShareRow({ journal }: { journal: Journal }) {
  const [copied, setCopied] = useState<"idle" | "done" | "failed">("idle");
  const link = () => {
    const base = journal.site.site_url;
    return base ? `${base.replace(/\/+$/, "")}/${window.location.hash}` : window.location.href;
  };
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(link());
      setCopied("done");
    } catch {
      setCopied("failed");
    }
    window.setTimeout(() => setCopied("idle"), 2000);
  };
  return (
    <div className="flex items-center gap-2 pt-2">
      <ButtonLink label="Follow on X" variant="primary" href={journal.site.x_url} target="_blank" rel="noopener noreferrer" />
      <Button
        label={copied === "done" ? "Link copied" : copied === "failed" ? "Couldn't copy" : "Copy link"}
        showIcon={false}
        onClick={copy}
      />
      <span role="status" className="sr-only">
        {copied === "done" ? "Link copied" : ""}
      </span>
    </div>
  );
}
