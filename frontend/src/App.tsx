import { RouterProvider } from "@tanstack/react-router";

import { useLiveEvents } from "./hooks/useLiveEvents";
import { router } from "./router";

export function App() {
  useLiveEvents();
  return <RouterProvider router={router} />;
}
