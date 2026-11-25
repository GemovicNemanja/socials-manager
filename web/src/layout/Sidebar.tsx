import type { ShellResponse } from "../api/models";
import { useShell } from "../api/hooks";
import { AccountSwitcher } from "../components/AccountSwitcher";
import type { NavIconName } from "../components/icons";
import { NavItem } from "../components/NavItem";
import { SectionLabel } from "../components/SectionLabel";
import { SyncStatus, type SyncStatusTone } from "../components/SyncStatus";
import { formatDateTime, formatInt, formatUsd } from "../lib/format";
import { href, type Route } from "../lib/router";

interface NavEntry {
  route: Route;
  label: string;
  icon: NavIconName;
  count?: (shell: ShellResponse) => number;
}

const groups: { label: string; items: NavEntry[] }[] = [
  {
    label: "Monitor",
    items: [
      { route: "overview", label: "Overview", icon: "NavOverview" },
      { route: "posts", label: "Posts", icon: "NavPosts", count: (s) => s.counts.posts },
      { route: "account", label: "Account", icon: "NavAccount" },
    ],
  },
  {
    label: "Model",
    items: [{ route: "forecasts", label: "Forecasts", icon: "NavForecasts" }],
  },
  {
    label: "Learn",
    items: [
      {
        route: "experiments",
        label: "Experiments",
        icon: "NavExperiments",
        count: (s) => s.counts.experiments_running,
      },
    ],
  },
];

function syncState(shell: ShellResponse | undefined, failed: boolean): { text: string; tone: SyncStatusTone } {
  if (failed) return { text: "API unavailable", tone: "negative" };
  if (!shell) return { text: "Loading…", tone: "faint" };
  const run = shell.last_run;
  if (!run) return { text: "No run yet", tone: "faint" };
  const when = run.finished_at ? formatDateTime(run.finished_at, shell.account?.tz ?? "UTC") : null;
  switch (run.status) {
    case "running":
      return { text: "Run in progress", tone: "warn" };
    case "failed":
      return { text: when ? `Run failed ${when}` : "Last run failed", tone: "negative" };
    default:
      return { text: when ? `Last run ${when}` : "Last run finished", tone: "positive" };
  }
}

export function Sidebar({ route }: { route: Route }) {
  const shell = useShell();
  const data = shell.data;
  const sync = syncState(data, shell.isError && !data);
  const spend = data?.spend;

  return (
    <nav aria-label="Main" className="flex w-[216px] shrink-0 flex-col border-r border-rule bg-ground">
      <AccountSwitcher
        initials={data?.account?.initials ?? "··"}
        handle={data?.account?.handle ?? (shell.isError ? "No connection" : data ? "No account yet" : "Loading…")}
        platform={data?.account?.platform_label ?? "Social Manager"}
      />
      <div className="flex flex-1 flex-col gap-4 overflow-y-auto px-2 pt-3">
        {groups.map((group) => (
          <div key={group.label} className="flex flex-col gap-0.5">
            <SectionLabel label={group.label} />
            {group.items.map((item) => {
              const count = data && item.count ? item.count(data) : undefined;
              return (
                <NavItem
                  key={item.route}
                  href={href(item.route)}
                  label={item.label}
                  icon={item.icon}
                  count={count ? formatInt(count) : undefined}
                  showCount={count != null && count > 0}
                  current={route === item.route ? "active" : "default"}
                />
              );
            })}
          </div>
        ))}
      </div>
      <SyncStatus
        syncText={sync.text}
        syncTone={sync.tone}
        spendLabel={spend ? `X API spend, ${spend.month_label}` : "X API spend"}
        spendValue={spend ? `${formatUsd(spend.spent_usd)} / ${formatUsd(spend.cap_usd)}` : "—"}
        spendFraction={spend && spend.cap_usd > 0 ? spend.spent_usd / spend.cap_usd : 0}
      />
    </nav>
  );
}
