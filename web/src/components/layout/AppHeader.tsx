import { Sparkles } from "lucide-react";
import { useWorkspaceStore } from "../../store/workspaceStore";

export function AppHeader() {
  const settings = useWorkspaceStore((state) => state.settings);
  const mode = useWorkspaceStore((state) => state.mode);

  return (
    <header className="app-header">
      <div>
        <div className="app-title">Competitive Radar</div>
        <div className="app-caption">AI 驱动的竞品分析研究工作台</div>
      </div>
      <div className="header-pills">
        <span className={`pill ${settings.use_mock ? "warn" : "ok"}`}>
          {settings.use_mock ? "Mock" : "LLM"}
        </span>
        <span className="pill">Reviewer: {settings.reviewer_mode}</span>
        <span className="pill">
          <Sparkles size={14} />
          {mode}
        </span>
      </div>
    </header>
  );
}
