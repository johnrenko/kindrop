import { createRootRoute, createRoute, createRouter } from "@tanstack/react-router";

import { Shell } from "./components/Shell";
import { DashboardPage } from "./pages/DashboardPage";
import { JobsPage } from "./pages/JobsPage";
import { KindleFilesPage } from "./pages/KindleFilesPage";
import { ReviewPage } from "./pages/ReviewPage";
import { SettingsPage } from "./pages/SettingsPage";

const rootRoute = createRootRoute({ component: Shell });
const indexRoute = createRoute({ getParentRoute: () => rootRoute, path: "/", component: DashboardPage });
const reviewRoute = createRoute({ getParentRoute: () => rootRoute, path: "/review", component: ReviewPage });
const jobsRoute = createRoute({ getParentRoute: () => rootRoute, path: "/jobs", component: JobsPage });
const kindleFilesRoute = createRoute({ getParentRoute: () => rootRoute, path: "/kindle", component: KindleFilesPage });
const settingsRoute = createRoute({ getParentRoute: () => rootRoute, path: "/settings", component: SettingsPage });

const routeTree = rootRoute.addChildren([indexRoute, reviewRoute, jobsRoute, kindleFilesRoute, settingsRoute]);
export const router = createRouter({ routeTree, defaultPreload: "intent" });

declare module "@tanstack/react-router" {
  interface Register { router: typeof router; }
}
