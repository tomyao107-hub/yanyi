import { createBrowserRouter } from "react-router-dom";
import { AppShell } from "./components/AppShell";
import { RouteErrorPage } from "./components/PageError";
import { LibraryPage } from "./pages/LibraryPage";
import { SettingsPage } from "./pages/SettingsPage";
import { WorkbenchPage } from "./pages/WorkbenchPage";

export const router = createBrowserRouter([
  {
    path: "/",
    element: <AppShell />,
    errorElement: <RouteErrorPage />,
    children: [
      { index: true, element: <LibraryPage /> },
      { path: "projects/:projectId", element: <WorkbenchPage /> },
      { path: "settings", element: <SettingsPage /> },
    ],
  },
]);
