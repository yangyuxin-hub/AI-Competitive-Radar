import { Bot, FileSearch, Folder, Plus, Radar, Settings } from "lucide-react";
import { useWorkspaceStore } from "../../store/workspaceStore";

export function Sidebar() {
  const domains = useWorkspaceStore((state) => state.domains);
  const settings = useWorkspaceStore((state) => state.settings);
  const updateSettings = useWorkspaceStore((state) => state.updateSettings);
  const reset = useWorkspaceStore((state) => state.reset);

  return (
    <aside className="sidebar">
      <div className="brand">
        <div className="brand-mark">CR</div>
        <div>
          <div className="brand-title">Competitive Radar</div>
          <div className="brand-subtitle">Evidence first</div>
        </div>
      </div>

      <button className="side-action active" onClick={reset}>
        <Plus size={18} />
        新分析
      </button>
      <button className="side-action">
        <FileSearch size={18} />
        搜索报告
      </button>
      <button className="side-action">
        <Folder size={18} />
        项目
      </button>
      <button className="side-action">
        <Bot size={18} />
        Agent
      </button>

      <div className="side-section">最近</div>
      <div className="recent-list">
        <span>Cursor 代码补全差距</span>
        <span>Notion 任务管理对比</span>
        <span>Reviewer 打回闭环演示</span>
      </div>

      <div className="settings-panel">
        <div className="settings-title">
          <Settings size={16} />
          运行设置
        </div>
        <label>
          默认行业
          <select
            value={settings.domain_key}
            onChange={(event) => updateSettings({ domain_key: event.target.value })}
          >
            {Object.entries(domains).map(([key, cfg]) => (
              <option key={key} value={key}>
                {cfg.name ?? key} · {key}
              </option>
            ))}
          </select>
        </label>
        <label>
          Reviewer
          <select
            value={settings.reviewer_mode}
            onChange={(event) =>
              updateSettings({ reviewer_mode: event.target.value as "minimal" | "full" })
            }
          >
            <option value="minimal">minimal</option>
            <option value="full">full</option>
          </select>
        </label>
        <label className="toggle-row">
          <input
            type="checkbox"
            checked={settings.use_mock}
            onChange={(event) => updateSettings({ use_mock: event.target.checked })}
          />
          Mock 模式
        </label>
        <label className="toggle-row">
          <input
            type="checkbox"
            checked={settings.enable_live}
            disabled={settings.use_mock}
            onChange={(event) => updateSettings({ enable_live: event.target.checked })}
          />
          实时抓取官网
        </label>
        <label className="toggle-row">
          <input
            type="checkbox"
            checked={settings.demo_loop}
            onChange={(event) => updateSettings({ demo_loop: event.target.checked })}
          />
          演示打回闭环
        </label>
      </div>

      <div className="side-footer">
        <Radar size={16} />
        本地 Demo
      </div>
    </aside>
  );
}
