import { useEffect } from "react";
import { getDomains } from "./api/client";
import { Sidebar } from "./components/layout/Sidebar";
import { AppHeader } from "./components/layout/AppHeader";
import { ChatPanel } from "./components/chat/ChatPanel";
import { ReportWorkspace } from "./components/report/ReportWorkspace";
import { useWorkspaceStore } from "./store/workspaceStore";

export default function App() {
  const mode = useWorkspaceStore((state) => state.mode);
  const setDomains = useWorkspaceStore((state) => state.setDomains);
  const setError = useWorkspaceStore((state) => state.setError);

  useEffect(() => {
    getDomains()
      .then((res) => setDomains(res.domains, res.products))
      .catch((error) => setError(error instanceof Error ? error.message : String(error)));
  }, [setDomains, setError]);

  return (
    <div className="app-shell">
      <Sidebar />
      <main className="main-shell">
        <AppHeader />
        {mode === "home" ? (
          <div className="home-layout">
            <ChatPanel home />
          </div>
        ) : (
          <div className="workspace-grid">
            <ChatPanel />
            <ReportWorkspace />
          </div>
        )}
      </main>
    </div>
  );
}
