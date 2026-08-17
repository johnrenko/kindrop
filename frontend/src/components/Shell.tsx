import { Link, Outlet, useRouterState } from "@tanstack/react-router";
import { BookOpen, History, Inbox, Settings } from "lucide-react";

const navigation = [
  { to: "/", label: "Desk", icon: BookOpen },
  { to: "/review", label: "Review", icon: Inbox },
  { to: "/jobs", label: "History", icon: History },
  { to: "/settings", label: "Settings", icon: Settings },
] as const;

export function Shell() {
  const pathname = useRouterState({ select: (state) => state.location.pathname });
  return (
    <div className="app-shell">
      <aside className="side-rail">
        <Link to="/" className="wordmark" aria-label="Kindrop home">
          <span className="wordmark__seal">K</span>
          <span>
            <strong>Kindrop</strong>
            <small>Drive to Kindle</small>
          </span>
        </Link>
        <nav aria-label="Primary navigation">
          {navigation.map(({ to, label, icon: Icon }, index) => {
            const active = to === "/" ? pathname === "/" : pathname.startsWith(to);
            return (
              <Link key={to} to={to} className={active ? "nav-link is-active" : "nav-link"}>
                <span className="nav-link__number">0{index + 1}</span>
                <Icon size={18} strokeWidth={1.8} aria-hidden="true" />
                <span>{label}</span>
              </Link>
            );
          })}
        </nav>
        <p className="side-note">Local only<br />One careful shelf at a time.</p>
      </aside>
      <main id="main-content" className="page-canvas">
        <Outlet />
      </main>
    </div>
  );
}

