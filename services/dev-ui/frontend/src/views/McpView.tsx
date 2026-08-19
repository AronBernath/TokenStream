import { FilePlus2, Save } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { adminFetch } from "../api";
import { Card, Field, JsonTextarea, PageActions } from "../components/Primitives";
import type { ViewProps } from "../types";
import { asErrorMessage, parseJsonEditor, pretty } from "../utils";

type McpServer = {
  name: string;
  [key: string]: unknown;
};

type McpSettings = {
  selected_servers?: string[];
  servers?: McpServer[];
  timeout_s?: number;
  strict?: boolean;
  max_tool_rounds?: number;
};

export function McpView({ refreshNonce, notify }: ViewProps) {
  const [serversJson, setServersJson] = useState("");
  const [availableServers, setAvailableServers] = useState<string[]>([]);
  const [selectedServers, setSelectedServers] = useState<string[]>([]);
  const [timeoutS, setTimeoutS] = useState(45);
  const [maxToolRounds, setMaxToolRounds] = useState(6);
  const [strict, setStrict] = useState(false);

  const load = useCallback(async () => {
    notify("Loading MCP settings...", "info");
    const data = await adminFetch<McpSettings>("mcp-settings");
    const servers = data.servers || [];
    setServersJson(pretty(servers));
    setAvailableServers(servers.map((item) => item.name).filter(Boolean));
    setSelectedServers(data.selected_servers || []);
    setTimeoutS(data.timeout_s || 45);
    setMaxToolRounds(data.max_tool_rounds || 6);
    setStrict(Boolean(data.strict));
    notify("MCP settings loaded.", "success");
  }, [notify]);

  useEffect(() => {
    load().catch((error) => notify(asErrorMessage(error), "error"));
  }, [load, notify, refreshNonce]);

  function addTemplate() {
    try {
      const servers = parseJsonEditor<McpServer[]>(serversJson, []);
      servers.push({
        name: `server_${servers.length + 1}`,
        transport: "streamable_http",
        url: "http://host:8000/mcp",
        namespace: null,
        headers: {}
      });
      setServersJson(pretty(servers));
      setAvailableServers(servers.map((item) => item.name).filter(Boolean));
      notify("MCP server template added to editor.", "success");
    } catch (error) {
      notify(asErrorMessage(error), "error");
    }
  }

  async function save(event: React.FormEvent) {
    event.preventDefault();
    notify("Saving MCP settings...", "info");
    const servers = parseJsonEditor<McpServer[]>(serversJson, []);
    await adminFetch("mcp-settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        selected_servers: selectedServers,
        servers,
        timeout_s: timeoutS,
        strict,
        max_tool_rounds: maxToolRounds
      })
    });
    await load();
    notify("MCP settings saved.", "success");
  }

  return (
    <Card className="editor-card">
      <form onSubmit={(event) => save(event).catch((error) => notify(asErrorMessage(error), "error"))}>
        <div className="section-heading">
          <div>
            <h2>MCP Server Registry</h2>
            <p>Define available MCP servers in JSON, then select the active set used at runtime.</p>
          </div>
          <PageActions>
            <button className="button secondary" type="button" onClick={addTemplate}>
              <FilePlus2 size={16} />
              <span>Add Template</span>
            </button>
            <button className="button primary" type="submit">
              <Save size={16} />
              <span>Save Settings</span>
            </button>
          </PageActions>
        </div>

        <div className="form-grid compact">
          <Field label="Selected MCP Servers" hint="Hold Ctrl or Shift to select multiple servers.">
            <select
              multiple
              value={selectedServers}
              onChange={(event) => setSelectedServers(Array.from(event.target.selectedOptions).map((option) => option.value))}
            >
              {availableServers.map((serverName) => (
                <option key={serverName} value={serverName}>
                  {serverName}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Timeout (seconds)">
            <input type="number" min={1} step={1} value={timeoutS} onChange={(event) => setTimeoutS(Number(event.target.value || "45"))} />
          </Field>
          <Field label="Max Tool Rounds">
            <input type="number" min={1} step={1} value={maxToolRounds} onChange={(event) => setMaxToolRounds(Number(event.target.value || "6"))} />
          </Field>
          <label className="check-field">
            <input type="checkbox" checked={strict} onChange={(event) => setStrict(event.target.checked)} />
            <span>Strict startup</span>
          </label>
        </div>

        <Field label="MCP Server Definitions JSON">
          <JsonTextarea value={serversJson} onChange={setServersJson} />
        </Field>
      </form>
    </Card>
  );
}
