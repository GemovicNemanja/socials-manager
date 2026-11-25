import { Sidebar } from "./layout/Sidebar";
import { useRoute, type Route } from "./lib/router";
import { OverviewPage } from "./pages/OverviewPage";
import { PlaceholderPage } from "./pages/PlaceholderPage";
import { PostsPage } from "./pages/PostsPage";

const placeholders: Record<Exclude<Route, "posts" | "overview">, { title: string; milestone: string }> = {
  account: { title: "Account", milestone: "M2" },
  forecasts: { title: "Forecasts", milestone: "M3" },
  experiments: { title: "Experiments", milestone: "M2" },
};

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
        ) : (
          <PlaceholderPage {...placeholders[route]} />
        )}
      </main>
    </div>
  );
}
