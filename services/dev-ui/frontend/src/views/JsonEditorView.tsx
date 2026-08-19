import { FilePlus2, Save, RotateCcw } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { adminFetch, jsonOptions } from "../api";
import { Card, JsonTextarea, PageActions } from "../components/Primitives";
import type { ViewProps } from "../types";
import { asErrorMessage, parseJsonEditor, pretty } from "../utils";

type Mode = "providers" | "policies" | "users";

const modeConfig = {
  providers: {
    endpoint: "providers",
    title: "Provider Registry",
    body: "Store provider metadata and secret references such as env://OPENAI_API_KEY. Secret values stay outside the registry.",
    saveLabel: "Save Providers"
  },
  policies: {
    endpoint: "policies",
    title: "Pipeline Policies",
    body: "Edit pipeline constraints, allowed tools, provider choices, corpus limits, and token budgets.",
    saveLabel: "Save Policies"
  },
  users: {
    endpoint: "users",
    title: "Human Users",
    body: "Passwords are write-only. Include a password field for a user only when rotating it.",
    saveLabel: "Save Users"
  }
};

export function JsonEditorView({ refreshNonce, notify, mode }: ViewProps & { mode: Mode }) {
  const config = modeConfig[mode];
  const [raw, setRaw] = useState("");

  const canAddTemplate = mode !== "users";

  const load = useMemo(
    () => async () => {
      notify(`Loading ${config.title.toLowerCase()}...`, "info");
      const data = await adminFetch<unknown>(config.endpoint);
      setRaw(pretty(data));
      notify(`${config.title} loaded.`, "success");
    },
    [config.endpoint, config.title, notify]
  );

  useEffect(() => {
    load().catch((error) => notify(asErrorMessage(error), "error"));
  }, [load, notify, refreshNonce]);

  async function save() {
    try {
      notify(`Saving ${config.title.toLowerCase()}...`, "info");
      const payload = parseJsonEditor<unknown[]>(raw, []);
      await adminFetch(config.endpoint, jsonOptions(payload));
      notify(`${config.title} saved.`, "success");
    } catch (error) {
      notify(asErrorMessage(error), "error");
    }
  }

  async function addTemplate() {
    try {
      const entries = parseJsonEditor<Record<string, unknown>[]>(raw, []);
      if (mode === "providers") {
        entries.push({
          name: `provider_${entries.length + 1}`,
          type: "ollama",
          base_url: "http://localhost:11434",
          require_api_key: false,
          default_model: "llama3",
          models: ["llama3"],
          capabilities: {
            tools: false,
            json_schema: false,
            streaming: false,
            max_context_window: 8192,
            default_context_window: 8192
          },
          client_controls: {
            temperature: true,
            max_tokens: true,
            context_length: true,
            context_length_param: "num_ctx"
          },
          secret_ref: null,
          secret_source_type: null
        });
      } else if (mode === "policies") {
        const rag = await adminFetch<{ default_corpus_id?: string; selected_corpus_ids?: string[] }>("rag-settings");
        const selectedCorpora = rag.selected_corpus_ids || [];
        const defaultCorpus = rag.default_corpus_id || selectedCorpora[0] || "default";
        entries.push({
          pipeline_id: `policy_${entries.length + 1}`,
          default_corpus_id: defaultCorpus,
          allowed_corpus_ids: selectedCorpora.length ? selectedCorpora : [defaultCorpus],
          default_filters: {},
          allowed_tools: ["rag"],
          allowed_providers: null,
          allowed_models: null,
          max_input_tokens: null,
          max_output_tokens: null,
          max_total_tokens: null,
          max_top_k: null,
          default_provider: null,
          default_model: null
        });
      }
      setRaw(pretty(entries));
      notify("Template added to editor.", "success");
    } catch (error) {
      notify(asErrorMessage(error), "error");
    }
  }

  return (
    <Card className="editor-card">
      <div className="section-heading">
        <div>
          <h2>{config.title}</h2>
          <p>{config.body}</p>
        </div>
        <PageActions>
          <button className="button secondary" type="button" onClick={() => load().catch((error) => notify(asErrorMessage(error), "error"))}>
            <RotateCcw size={16} />
            <span>Reload</span>
          </button>
          {canAddTemplate ? (
            <button className="button secondary" type="button" onClick={addTemplate}>
              <FilePlus2 size={16} />
              <span>Add Template</span>
            </button>
          ) : null}
          <button className="button primary" type="button" onClick={save}>
            <Save size={16} />
            <span>{config.saveLabel}</span>
          </button>
        </PageActions>
      </div>
      <JsonTextarea value={raw} onChange={setRaw} />
    </Card>
  );
}
