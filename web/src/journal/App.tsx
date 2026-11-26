import { useEffect } from "react";
import { BackLink, Column, Lede, Title, TopBar } from "./components/Layout";
import { findEntry, findExperiment, useJournal, type Journal } from "./data";
import { EntryPage } from "./pages/EntryPage";
import { ExperimentPage } from "./pages/ExperimentPage";
import { ExperimentsPage } from "./pages/ExperimentsPage";
import { HomePage } from "./pages/HomePage";
import { MethodPage } from "./pages/MethodPage";
import { paths, useJournalRoute, type JournalRoute } from "./router";

/** The browser tab title for a route: "Week 5: … · @handle". */
function pageTitle(journal: Journal | undefined, route: JournalRoute): string {
  const site = journal ? `${journal.site.handle} · ${journal.site.tagline}` : "Journal";
  if (!journal) return site;
  switch (route.page) {
    case "entry": {
      const e = findEntry(journal, route.slug);
      return e ? `Week ${e.week_number}: ${e.title} · ${journal.site.handle}` : `Note not found · ${site}`;
    }
    case "experiments":
      return `Experiments · ${site}`;
    case "experiment": {
      const x = findExperiment(journal, route.slug);
      return x ? `${x.key}: ${x.question} · ${journal.site.handle}` : `Experiment not found · ${site}`;
    }
    case "method":
      return `Method · ${site}`;
    case "not-found":
      return `Page not found · ${site}`;
    default:
      return site;
  }
}

function NotFound({ what, back, backLabel }: { what: string; back: string; backLabel: string }) {
  return (
    <Column label="Not found">
      <div className="flex flex-col gap-3">
        <BackLink href={back} label={backLabel} />
        <Title>{what}</Title>
        <Lede>It may not be published yet, or the link is mistyped.</Lede>
      </div>
    </Column>
  );
}

function Page({ journal, route }: { journal: Journal; route: JournalRoute }) {
  switch (route.page) {
    case "home":
      return <HomePage journal={journal} />;
    case "entry": {
      const entry = findEntry(journal, route.slug);
      return entry ? (
        <EntryPage key={entry.slug} journal={journal} entry={entry} />
      ) : (
        <NotFound what="There's no weekly note here" back={paths.home} backLabel="All weekly notes" />
      );
    }
    case "experiments":
      return <ExperimentsPage journal={journal} />;
    case "experiment": {
      const x = findExperiment(journal, route.slug);
      return x ? (
        <ExperimentPage key={x.key} journal={journal} experiment={x} />
      ) : (
        <NotFound what="There's no experiment here" back={paths.experiments} backLabel="All experiments" />
      );
    }
    case "method":
      return <MethodPage journal={journal} />;
    default:
      return <NotFound what="There's no page here" back={paths.home} backLabel="Journal" />;
  }
}

export function App() {
  const route = useJournalRoute();
  const query = useJournal();
  const journal = query.data?.journal;
  const routeKey = JSON.stringify(route);

  useEffect(() => {
    document.title = pageTitle(journal, route);
    // routeKey stands for route (a new object each render).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [journal, routeKey]);

  useEffect(() => {
    window.scrollTo(0, 0);
  }, [routeKey]);

  return (
    <div className="min-h-full bg-ground font-sans text-base text-text">
      <TopBar journal={journal ?? null} route={route} />
      <div>
        {journal ? (
          <Page journal={journal} route={route} />
        ) : query.isError ? (
          <Column label="Error">
            <Title>The journal couldn&rsquo;t load</Title>
            <Lede>Its data file is missing or unreadable. Try again in a minute.</Lede>
            <p className="font-mono text-xs text-faint">{String(query.error)}</p>
          </Column>
        ) : (
          <Column label="Loading">
            <p role="status" className="text-sm text-faint">
              Loading the journal…
            </p>
          </Column>
        )}
      </div>
    </div>
  );
}
