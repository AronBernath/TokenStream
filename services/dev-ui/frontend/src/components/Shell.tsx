import { FileJson, LogOut, Menu, RefreshCw, X } from "lucide-react";
import type { ReactNode } from "react";
import { navItems, type NavItem } from "../navigation";
import type { Session, Toast, ViewId } from "../types";

const routeMeta: Partial<Record<ViewId, Pick<NavItem, "title" | "description">>> = {
  "corpora/details": {
    title: "Corpus Details",
    description: "Inspect ingestion quality, index write counts, source hashes, and job statistics."
  }
};

function isActiveNavItem(item: NavItem, currentView: ViewId) {
  return item.id === currentView || currentView.startsWith(`${item.id}/`);
}

type ShellProps = {
  session: Session;
  currentView: ViewId;
  sidebarOpen: boolean;
  onNavigate: (view: ViewId) => void;
  onToggleSidebar: () => void;
  onCloseSidebar: () => void;
  onRefresh: () => void;
  onLogout: () => void;
  children: ReactNode;
};

export function Shell({
  session,
  currentView,
  sidebarOpen,
  onNavigate,
  onToggleSidebar,
  onCloseSidebar,
  onRefresh,
  onLogout,
  children
}: ShellProps) {
  const activeNavItem = navItems.find((item) => isActiveNavItem(item, currentView)) ?? navItems[0];
  const activeMeta = routeMeta[currentView] ?? activeNavItem;
  const roleText = session.roles?.length ? session.roles.join(", ") : "No roles";

  return (
    <div className="app-frame">
      <aside className={`sidebar ${sidebarOpen ? "is-open" : ""}`}>
        <div className="brand">
          <div className="brand-mark">
            <FileJson size={20} />
          </div>
          <div>
            <div className="brand-name">TokenStream</div>
            <div className="brand-subtitle">Admin Console</div>
          </div>
          <button className="icon-button sidebar-close" type="button" onClick={onCloseSidebar} aria-label="Close menu">
            <X size={18} />
          </button>
        </div>

        <nav className="nav-list" aria-label="Primary navigation">
          {navItems.map((item) => {
            const Icon = item.icon;
            return (
              <button
                key={item.id}
                className={`nav-item ${isActiveNavItem(item, currentView) ? "active" : ""}`}
                type="button"
                onClick={() => onNavigate(item.id)}
              >
                <Icon size={18} />
                <span>{item.label}</span>
              </button>
            );
          })}
        </nav>
      </aside>

      {sidebarOpen ? <button className="scrim" type="button" aria-label="Close menu" onClick={onCloseSidebar} /> : null}

      <div className="workspace">
        <header className="topbar">
          <button className="icon-button menu-button" type="button" onClick={onToggleSidebar} aria-label="Open menu">
            <Menu size={20} />
          </button>

          <div className="page-heading">
            <h1>{activeMeta.title}</h1>
            <p>{activeMeta.description}</p>
          </div>

          <div className="topbar-actions">
            <button className="button secondary" type="button" onClick={onRefresh}>
              <RefreshCw size={16} />
              <span>Refresh</span>
            </button>
            <div className="session-chip" title={(session.permissions || []).join(", ")}>
              <div className="avatar">{session.username.slice(0, 1).toUpperCase()}</div>
              <div>
                <strong>{session.username}</strong>
                <span>{roleText}</span>
              </div>
            </div>
            <button className="button danger subtle" type="button" onClick={onLogout}>
              <LogOut size={16} />
              <span>Logout</span>
            </button>
          </div>
        </header>

        <main className="content">{children}</main>
      </div>
    </div>
  );
}

export function StatusToast({ toast, onDismiss }: { toast: Toast | null; onDismiss: () => void }) {
  if (!toast?.message) return null;
  return (
    <div className={`toast ${toast.kind}`} role="status">
      <div>
        <strong>{toast.kind === "error" ? "Action needed" : toast.kind === "success" ? "Done" : "Status"}</strong>
        <p>{toast.message}</p>
      </div>
      <button className="icon-button" type="button" onClick={onDismiss} aria-label="Dismiss status">
        <X size={16} />
      </button>
    </div>
  );
}
