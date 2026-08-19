import { KeyRound, Plus, Trash2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { adminFetch } from "../api";
import { Card, EmptyState, PageActions } from "../components/Primitives";
import type { ViewProps } from "../types";
import { asErrorMessage, formatList } from "../utils";

type MachineKey = {
  key_id: string;
  subject?: string;
  scopes?: string[];
};

export function KeysView({ refreshNonce, notify }: ViewProps) {
  const [keys, setKeys] = useState<MachineKey[]>([]);

  const load = useCallback(async () => {
    notify("Loading machine API keys...", "info");
    const data = await adminFetch<MachineKey[]>("api-keys");
    setKeys(data);
    notify("Machine API keys loaded.", "success");
  }, [notify]);

  useEffect(() => {
    load().catch((error) => notify(asErrorMessage(error), "error"));
  }, [load, notify, refreshNonce]);

  async function createKey() {
    const subject = window.prompt("Subject for the machine API key:");
    if (!subject) return;
    const scopesRaw = window.prompt("Comma-separated scopes:", "models:list,chat:invoke,rag:query,tools:use");
    if (scopesRaw === null) return;
    const scopes = scopesRaw.split(",").map((scope) => scope.trim()).filter(Boolean);
    notify("Generating machine API key...", "info");
    const data = await adminFetch<{ key_id: string; plaintext_key: string }>("api-keys", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ subject, scopes })
    });
    window.alert(`Save this machine key now.\n\nKey ID: ${data.key_id}\nSecret: ${data.plaintext_key}`);
    await load();
    notify("Machine API key created.", "success");
  }

  async function revokeKey(keyId: string) {
    if (!window.confirm(`Revoke API key ${keyId}?`)) return;
    notify(`Revoking ${keyId}...`, "info");
    await adminFetch(`api-keys/${encodeURIComponent(keyId)}`, { method: "DELETE" });
    await load();
    notify("Machine API key revoked.", "success");
  }

  return (
    <Card>
      <div className="section-heading">
        <div>
          <h2>Machine API Keys</h2>
          <p>Programmatic keys for services and automation clients. Human users authenticate separately.</p>
        </div>
        <PageActions>
          <button className="button primary" type="button" onClick={() => createKey().catch((error) => notify(asErrorMessage(error), "error"))}>
            <Plus size={16} />
            <span>Generate Key</span>
          </button>
        </PageActions>
      </div>

      {keys.length ? (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Key ID</th>
                <th>Subject</th>
                <th>Scopes</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {keys.map((item) => (
                <tr key={item.key_id}>
                  <td>
                    <code>{item.key_id}</code>
                  </td>
                  <td>{item.subject || "-"}</td>
                  <td>{formatList(item.scopes)}</td>
                  <td>
                    <button className="icon-button danger" type="button" onClick={() => revokeKey(item.key_id).catch((error) => notify(asErrorMessage(error), "error"))} title="Revoke key">
                      <Trash2 size={16} />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <EmptyState title="No machine keys" body="Generate a key when a service or automation client needs programmatic access." />
      )}
      <div className="inline-note">
        <KeyRound size={16} />
        <span>Plaintext secrets are shown once at creation time.</span>
      </div>
    </Card>
  );
}
