import { useCallback, useEffect, useMemo, useState } from "react";
import { authFetch } from "./api";
import { Shell, StatusToast } from "./components/Shell";
import { navItems } from "./navigation";
import { DashboardView } from "./views/DashboardView";
import { JsonEditorView } from "./views/JsonEditorView";
import { KeysView } from "./views/KeysView";
import { RagView } from "./views/RagView";
import { McpView } from "./views/McpView";
import { CorporaView } from "./views/CorporaView";
import { CorpusDetailsView } from "./views/CorpusDetailsView";
import type { Session, Toast, ToastKind, ViewId } from "./types";
import { asErrorMessage } from "./utils";

function viewFromLocation(): ViewId {
  const rawHash = window.location.hash || "#dashboard";
  const [route, query = ""] = rawHash.replace(/^#/, "").split("?");
  if (route === "corpora/artifact_view") {
    const nextHash = `#corpora/details${query ? `?${query}` : ""}`;
    window.history.replaceState(null, "", `${window.location.pathname}${window.location.search}${nextHash}`);
    return "corpora/details";
  }
  const routeViewIds: ViewId[] = [...navItems.map((item) => item.id), "corpora/details"];
  return routeViewIds.includes(route as ViewId) ? (route as ViewId) : "dashboard";
}

function LoginScreen({ onLogin, notify }: { onLogin: (session: Session) => void; notify: (message: string, kind?: ToastKind) => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setSubmitting(true);
    notify("Signing in...", "info");
    try {
      const session = await authFetch<Session>("login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: username.trim(), password })
      });
      onLogin(session);
      notify(session.must_rotate_password ? "This bootstrap account should have its password rotated immediately." : "Signed in.", session.must_rotate_password ? "error" : "success");
    } catch (error) {
      notify(asErrorMessage(error, "Login failed."), "error");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="login-page">
      <section className="login-panel">
        <div className="login-brand">
          <div className="brand-mark large">OA</div>
          <div>
            <h1>TokenStream Admin</h1>
            <p>Runtime configuration for providers, corpora, RAG, MCP, users, and machine access.</p>
          </div>
        </div>
        <form onSubmit={submit} className="login-form">
          <label className="field">
            <span>Username</span>
            <input autoComplete="username" value={username} onChange={(event) => setUsername(event.target.value)} />
          </label>
          <label className="field">
            <span>Password</span>
            <input type="password" autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} />
          </label>
          <button className="button primary full" type="submit" disabled={submitting}>
            {submitting ? "Signing in..." : "Sign in"}
          </button>
        </form>
        <p className="login-note">Local development bootstrap account: admin / admin until rotated.</p>
      </section>
    </main>
  );
}

export function App() {
  const [session, setSession] = useState<Session | null>(null);
  const [currentView, setCurrentView] = useState<ViewId>(viewFromLocation);
  const [refreshNonce, setRefreshNonce] = useState(0);
  const [toast, setToast] = useState<Toast | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(false);

  const notify = useCallback((message: string, kind: ToastKind = "info") => {
    setToast({ message, kind });
  }, []);

  useEffect(() => {
    const onHashChange = () => setCurrentView(viewFromLocation());
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);

  useEffect(() => {
    authFetch<Session>("me")
      .then((nextSession) => {
        setSession(nextSession);
        if (nextSession.must_rotate_password) {
          notify("This bootstrap account should have its password rotated immediately.", "error");
        }
      })
      .catch(() => setSession(null));
  }, [notify]);

  const viewProps = useMemo(
    () => ({
      refreshNonce,
      notify
    }),
    [refreshNonce, notify]
  );

  async function logout() {
    try {
      await authFetch<{ status: string }>("logout", { method: "POST" });
    } catch {
      // Local shell state should reset even if the backend logout call is already expired.
    }
    setSession(null);
    notify("Logged out.", "success");
  }

  function navigate(view: ViewId) {
    window.location.hash = view;
    setCurrentView(view);
    setSidebarOpen(false);
  }

  let view = <DashboardView {...viewProps} />;
  if (currentView === "providers") {
    view = <JsonEditorView {...viewProps} mode="providers" />;
  } else if (currentView === "policies") {
    view = <JsonEditorView {...viewProps} mode="policies" />;
  } else if (currentView === "keys") {
    view = <KeysView {...viewProps} />;
  } else if (currentView === "users") {
    view = <JsonEditorView {...viewProps} mode="users" />;
  } else if (currentView === "corpora") {
    view = <CorporaView {...viewProps} />;
  } else if (currentView === "corpora/details") {
    view = <CorpusDetailsView {...viewProps} />;
  } else if (currentView === "rag") {
    view = <RagView {...viewProps} />;
  } else if (currentView === "mcp") {
    view = <McpView {...viewProps} />;
  }

  return (
    <>
      {session ? (
        <Shell
          session={session}
          currentView={currentView}
          sidebarOpen={sidebarOpen}
          onNavigate={navigate}
          onToggleSidebar={() => setSidebarOpen((value) => !value)}
          onCloseSidebar={() => setSidebarOpen(false)}
          onRefresh={() => setRefreshNonce((value) => value + 1)}
          onLogout={logout}
        >
          {view}
        </Shell>
      ) : (
        <LoginScreen onLogin={setSession} notify={notify} />
      )}
      <StatusToast toast={toast} onDismiss={() => setToast(null)} />
    </>
  );
}
