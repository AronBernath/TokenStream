import { Save } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { adminFetch } from "../api";
import { Card, Field, PageActions } from "../components/Primitives";
import type { ViewProps } from "../types";
import { asErrorMessage } from "../utils";

type RagSettings = {
  default_corpus_id?: string;
  selected_corpus_ids?: string[];
  default_top_k?: number;
  retrieval_api_url?: string;
};

export function RagView({ refreshNonce, notify }: ViewProps) {
  const [availableCorpora, setAvailableCorpora] = useState<string[]>([]);
  const [defaultCorpus, setDefaultCorpus] = useState("");
  const [selectedCorpora, setSelectedCorpora] = useState<string[]>([]);
  const [topK, setTopK] = useState(8);
  const [retrievalUrl, setRetrievalUrl] = useState("");

  const load = useCallback(async () => {
    notify("Loading RAG settings...", "info");
    const [data, corporaPayload] = await Promise.all([
      adminFetch<RagSettings>("rag-settings"),
      adminFetch<{ corpora?: string[] }>("corpora")
    ]);
    const corpora = Array.from(
      new Set([
        ...(corporaPayload.corpora || []),
        ...(data.selected_corpus_ids || []),
        data.default_corpus_id || ""
      ].filter(Boolean))
    );
    setAvailableCorpora(corpora);
    setDefaultCorpus(data.default_corpus_id || "");
    setSelectedCorpora(data.selected_corpus_ids || []);
    setTopK(data.default_top_k || 8);
    setRetrievalUrl(data.retrieval_api_url || "");
    notify("RAG settings loaded.", "success");
  }, [notify]);

  useEffect(() => {
    load().catch((error) => notify(asErrorMessage(error), "error"));
  }, [load, notify, refreshNonce]);

  async function save(event: React.FormEvent) {
    event.preventDefault();
    notify("Saving RAG settings...", "info");
    await adminFetch("rag-settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        default_corpus_id: defaultCorpus.trim(),
        selected_corpus_ids: selectedCorpora,
        default_top_k: topK,
        retrieval_api_url: retrievalUrl.trim()
      })
    });
    notify("RAG settings saved.", "success");
  }

  return (
    <Card>
      <form onSubmit={(event) => save(event).catch((error) => notify(asErrorMessage(error), "error"))}>
        <div className="section-heading">
          <div>
            <h2>RAG Runtime Settings</h2>
            <p>These settings control default corpus routing and retrieval behavior for orchestrator-api.</p>
          </div>
          <PageActions>
            <button className="button primary" type="submit">
              <Save size={16} />
              <span>Save Settings</span>
            </button>
          </PageActions>
        </div>

        <div className="form-grid">
          <Field label="Default Corpus ID">
            <input value={defaultCorpus} onChange={(event) => setDefaultCorpus(event.target.value)} />
          </Field>
          <Field label="Default Top K">
            <input type="number" min={1} value={topK} onChange={(event) => setTopK(Number(event.target.value || "8"))} />
          </Field>
          <Field label="Retrieval API URL">
            <input value={retrievalUrl} onChange={(event) => setRetrievalUrl(event.target.value)} />
          </Field>
          <Field label="Selected Corpora" hint="Hold Ctrl or Shift to select multiple corpora.">
            <select
              multiple
              value={selectedCorpora}
              onChange={(event) => setSelectedCorpora(Array.from(event.target.selectedOptions).map((option) => option.value))}
            >
              {availableCorpora.map((corpusId) => (
                <option key={corpusId} value={corpusId}>
                  {corpusId}
                </option>
              ))}
            </select>
          </Field>
        </div>
      </form>
    </Card>
  );
}
