import { Sidebar } from "./layout/Sidebar";
import { useRoute } from "./lib/router";
import { AccountPage } from "./pages/AccountPage";
import { ExperimentsPage } from "./pages/ExperimentsPage";
import { OverviewPage } from "./pages/OverviewPage";
import { PlaceholderPage } from "./pages/PlaceholderPage";
import { PostsPage } from "./pages/PostsPage";

export function App() {
  const route = useRoute();
  return (
    <div className="flex h-full min-w-[1280px] overflow-hidden bg-ground font-sans text-base text-text">
      <Sidebar route={route} />
      <main className="flex min-w-0 flex-1 flex-col">
        {route === "posts" ? (
          <PostsPage />
        ) : route === "overview" ? (
          <OverviewPage />
        ) : route === "experiments" ? (
          <ExperimentsPage />
        ) : route === "account" ? (
          <AccountPage />
        ) : (
          <PlaceholderPage title="Forecasts" milestone="M3" />
        )}
      </main>
    </div>
  );
}
