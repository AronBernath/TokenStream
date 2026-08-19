import { Archive, Download, FileUp, Link, Loader2, PencilLine, Plus, RefreshCw, Save, Trash2, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { adminFetch } from "../api";
import { Badge, Card, EmptyState, Field, JsonTextarea, PageActions } from "../components/Primitives";
import type {
  CorpusDetail,
  CorpusRegistryBundle,
  CorpusRegistryImportResult,
  CorpusSource,
  IngestionJob,
  ModelOption,
  PolicyRecord,
  ProviderRecord,
  ViewProps
} from "../types";
import { asErrorMessage, parseJsonEditor, pretty, statusTone } from "../utils";

const formats = ["html", "pdf", "markdown", "text", "yaml", "xlsx"];

type CorpusForm = {
  title: string;
  description: string;
  environment: string;
  tenantId: string;
  chunkingJson: string;
  indexJson: string;
  metadataJson: string;
};

function formFromDetail(detail: CorpusDetail): CorpusForm {
  return {
    title: detail.title || "",
    description: detail.description || "",
    environment: detail.environment || "",
    tenantId: detail.tenant_id || "",
    chunkingJson: pretty(detail.chunking || {}),
    indexJson: pretty(detail.index || {}),
    metadataJson: pretty(detail.metadata || {})
  };
}

function sourceSummary(item: CorpusSource) {
  const location = item.type === "url" ? item.url || "-" : item.object_uri || "-";
  return [
    location,
    item.content_hash ? `hash: ${item.content_hash}` : "",
    item.size_bytes != null ? `${item.size_bytes} bytes` : "",
    item.content_type || "",
    item.configuration && Object.keys(item.configuration).length ? `configuration: ${JSON.stringify(item.configuration)}` : "",
    item.metadata && Object.keys(item.metadata).length ? `metadata: ${JSON.stringify(item.metadata)}` : ""
  ].filter(Boolean);
}

function renderJobSummary(payload: IngestionJob | null) {
  if (!payload?.job_id) return "Select or create an ingestion job to see its progress.";
  const stats = payload.stats || {};
  const stage = stats.stage ? String(stats.stage).replace(/_/g, " ") : "";
  const total = stats.sources_total;
  const completed = stats.sources_completed || 0;
  const sourceProgress = total != null ? ` Sources: ${completed}/${total}.` : "";
  const error = payload.error ? ` Error: ${payload.error}` : "";
  return `Job ${payload.job_id}: ${payload.status || "unknown"}${stage ? ` - ${stage}.` : "."}${sourceProgress}${error}`;
}

function modelAllowed(option: ModelOption, allowedModels?: string[] | null) {
  if (!allowedModels?.length) return true;
  return allowedModels.includes(option.id) || allowedModels.includes(option.model);
}

function providerAllowed(provider: string, allowedProviders?: string[] | null) {
  return !allowedProviders?.length || allowedProviders.includes(provider);
}

function deriveChunkingModels(providers: ProviderRecord[], policies: PolicyRecord[], pipelineId: string) {
  const policy = policies.find((item) => item.pipeline_id === (pipelineId || "default")) || policies.find((item) => item.pipeline_id === "default");
  const chunking = policy?.chunking || {};
  if (!providers.length) return { options: [], blockedOptions: [], defaultId: "", hint: "No providers are configured." };
  if (policy && chunking.enabled === false) return { options: [], blockedOptions: [], defaultId: "", hint: `Policy ${policy.pipeline_id} has chunking disabled.` };
  const allowedProviders = chunking.allowed_providers;
  const allowedModels = chunking.allowed_models;
  const defaultId = chunking.default_provider && chunking.default_model ? `${chunking.default_provider}:${chunking.default_model}` : "";
  const chunkingProviders = providers.filter((provider) => provider.capabilities?.chunking);

  const allOptions = chunkingProviders.flatMap((provider) => {
    const models = provider.models?.length ? provider.models : provider.default_model ? [provider.default_model] : [];
    return models.map((model) => ({ id: `${provider.name}:${model}`, provider: provider.name, model }));
  });
  const options = allOptions.filter((option) => providerAllowed(option.provider, allowedProviders) && modelAllowed(option, allowedModels));
  const blockedOptions = allOptions.filter((option) => !options.some((allowed) => allowed.id === option.id));

  const enabledProviderNames = chunkingProviders.map((provider) => provider.name);
  let hint = options.length
    ? `${options.length} chunking model${options.length === 1 ? "" : "s"} available from ${enabledProviderNames.join(", ")}.`
    : "No providers are enabled for chunking.";
  if (blockedOptions.length) {
    hint += ` ${blockedOptions.length} provider-capable model${blockedOptions.length === 1 ? " is" : "s are"} blocked by policy.`;
  }
  if (!options.length && blockedOptions.length && allowedProviders?.length) {
    hint = `Chunking-enabled providers: ${enabledProviderNames.join(", ")}. Policy ${policy?.pipeline_id || pipelineId || "default"} allows providers: ${allowedProviders.join(", ")}.`;
  }
  if (!options.length && blockedOptions.length && allowedModels?.length) {
    hint = `Chunking-enabled providers: ${enabledProviderNames.join(", ")}. Policy ${policy?.pipeline_id || pipelineId || "default"} allows models: ${allowedModels.join(", ")}.`;
  }
  return { options, blockedOptions, defaultId, hint };
}

export function CorporaView({ refreshNonce, notify }: ViewProps) {
  const [corpora, setCorpora] = useState<string[]>([]);
  const [selectedCorpus, setSelectedCorpus] = useState("");
  const [detail, setDetail] = useState<CorpusDetail | null>(null);
  const [form, setForm] = useState<CorpusForm | null>(null);
  const [jobs, setJobs] = useState<IngestionJob[]>([]);
  const [selectedSources, setSelectedSources] = useState<Set<string>>(new Set());
  const [pipelineId, setPipelineId] = useState("default");
  const [chunkingModels, setChunkingModels] = useState<ModelOption[]>([]);
  const [blockedChunkingModels, setBlockedChunkingModels] = useState<ModelOption[]>([]);
  const [selectedChunkingModel, setSelectedChunkingModel] = useState("");
  const [chunkingModelHint, setChunkingModelHint] = useState("Loading chunking models...");
  const [jobStatus, setJobStatus] = useState<IngestionJob | null>(null);
  const [urlSource, setUrlSource] = useState({ id: "", title: "", url: "", format: "html" });
  const [editingUrlSourceId, setEditingUrlSourceId] = useState("");
  const [fileSource, setFileSource] = useState<{ id: string; title: string; format: string; file: File | null }>({
    id: "",
    title: "",
    format: "pdf",
    file: null
  });
  const pollRef = useRef<number | null>(null);
  const importRegistryInputRef = useRef<HTMLInputElement | null>(null);
  const selectedCorpusRef = useRef(selectedCorpus);
  const pipelineIdRef = useRef(pipelineId);

  useEffect(() => {
    selectedCorpusRef.current = selectedCorpus;
  }, [selectedCorpus]);

  useEffect(() => {
    pipelineIdRef.current = pipelineId;
  }, [pipelineId]);

  function clearPoll() {
    if (pollRef.current) {
      window.clearTimeout(pollRef.current);
      pollRef.current = null;
    }
  }

  useEffect(() => clearPoll, []);

  async function loadCorpusDetail(corpusId: string) {
    const nextDetail = await adminFetch<CorpusDetail>(`corpora/${encodeURIComponent(corpusId)}`);
    setDetail(nextDetail);
    setForm(formFromDetail(nextDetail));
    setSelectedSources(new Set());
    setEditingUrlSourceId("");
  }

  async function loadCorpusJobs(corpusId: string) {
    const allJobs = await adminFetch<IngestionJob[]>("ingestion-jobs");
    setJobs((allJobs || []).filter((job) => job.corpus_id === corpusId));
  }

  const loadChunkingModels = useCallback(
    async (nextPipelineId = pipelineIdRef.current, announce = false) => {
      setChunkingModelHint("Reloading chunking models...");
      const [providers, policies] = await Promise.all([
        adminFetch<ProviderRecord[]>("providers"),
        adminFetch<PolicyRecord[]>("policies")
      ]);
      const { options, blockedOptions, defaultId, hint } = deriveChunkingModels(
        providers || [],
        policies || [],
        nextPipelineId.trim() || "default"
      );
      setChunkingModels(options);
      setBlockedChunkingModels(blockedOptions);
      setChunkingModelHint(hint);
      setSelectedChunkingModel((current) => {
        if (current && options.some((option) => option.id === current)) return current;
        if (defaultId && options.some((option) => option.id === defaultId)) return defaultId;
        return options[0]?.id || "";
      });
      if (announce) {
        notify(
          options.length ? `Loaded ${options.length} chunking model${options.length === 1 ? "" : "s"}.` : hint,
          options.length ? "success" : "info"
        );
      }
    },
    [notify]
  );

  const loadCorpora = useCallback(
    async (preferred = selectedCorpusRef.current) => {
      notify("Loading corpora...", "info");
      const [catalog] = await Promise.all([adminFetch<{ corpora?: string[] }>("corpora"), loadChunkingModels()]);
      const nextCorpora = catalog.corpora || [];
      setCorpora(nextCorpora);

      if (!nextCorpora.length) {
        setSelectedCorpus("");
        setDetail(null);
        setForm(null);
        setJobs([]);
        setJobStatus({ status: "idle" });
        notify("No corpora discovered.", "info");
        return;
      }

      const nextSelected = nextCorpora.includes(preferred) ? preferred : nextCorpora[0];
      setSelectedCorpus(nextSelected);
      await loadCorpusDetail(nextSelected);
      await loadCorpusJobs(nextSelected);
      notify("Corpora loaded.", "success");
    },
    [loadChunkingModels, notify]
  );

  useEffect(() => {
    loadCorpora().catch((error) => notify(asErrorMessage(error), "error"));
  }, [loadCorpora, notify, refreshNonce]);

  useEffect(() => {
    loadChunkingModels(pipelineId).catch((error) => notify(asErrorMessage(error, "Unable to load chunking models."), "error"));
  }, [loadChunkingModels, notify, pipelineId]);

  async function changeCorpus(corpusId: string) {
    setSelectedCorpus(corpusId);
    await loadCorpusDetail(corpusId);
    await loadCorpusJobs(corpusId);
  }

  async function createCorpus() {
    const corpusId = window.prompt("Corpus ID:");
    if (!corpusId) return;
    notify("Creating corpus...", "info");
    await adminFetch("corpora", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ corpus_id: corpusId, title: corpusId })
    });
    await loadCorpora(corpusId);
    notify("Corpus created.", "success");
  }

  async function deleteCorpus() {
    if (!selectedCorpus) return;
    if (!window.confirm(`Delete corpus ${selectedCorpus}?`)) return;
    notify(`Deleting ${selectedCorpus}...`, "info");
    await adminFetch(`corpora/${encodeURIComponent(selectedCorpus)}`, { method: "DELETE" });
    await loadCorpora("");
    notify("Corpus deleted.", "success");
  }

  async function exportCorpusRegistry() {
    if (!selectedCorpus) throw new Error("Select a corpus first.");
    notify(`Exporting ${selectedCorpus} registry...`, "info");
    const bundle = await adminFetch<CorpusRegistryBundle>(`corpora/${encodeURIComponent(selectedCorpus)}/registry-export`);
    const blob = new Blob([`${JSON.stringify(bundle, null, 2)}\n`], { type: "application/json" });
    const link = document.createElement("a");
    const url = URL.createObjectURL(blob);
    link.href = url;
    link.download = `${bundle.corpus?.corpus_id || selectedCorpus}-registry.json`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
    notify("Corpus registry exported.", "success");
  }

  async function importCorpusRegistry(file: File | null) {
    if (!file) return;
    notify("Importing corpus registry...", "info");
    const bundle = JSON.parse(await file.text()) as CorpusRegistryBundle;
    const corpusId = bundle?.corpus?.corpus_id || "";
    if (!corpusId) throw new Error("Registry bundle is missing corpus.corpus_id.");
    const exists = corpora.includes(corpusId);
    if (exists && !window.confirm(`Corpus ${corpusId} already exists. Replace its registry metadata and sources?`)) {
      notify("Registry import cancelled.", "info");
      return;
    }
    const conflictStrategy = exists ? "replace" : "fail";
    const result = await adminFetch<CorpusRegistryImportResult>("corpora/registry-import", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ bundle, conflict_strategy: conflictStrategy })
    });
    await loadCorpora(result.corpus_id);
    notify(`Imported ${result.corpus_id} with ${result.sources_imported || 0} source${result.sources_imported === 1 ? "" : "s"}.`, "success");
  }

  async function saveCorpusDetails(event: React.FormEvent) {
    event.preventDefault();
    if (!selectedCorpus || !form) return;
    notify("Saving corpus details...", "info");
    await adminFetch(`corpora/${encodeURIComponent(selectedCorpus)}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        title: form.title || null,
        description: form.description || null,
        environment: form.environment || null,
        tenant_id: form.tenantId || null,
        chunking: parseJsonEditor(form.chunkingJson, {}),
        index: parseJsonEditor(form.indexJson, {}),
        metadata: parseJsonEditor(form.metadataJson, {})
      })
    });
    await loadCorpusDetail(selectedCorpus);
    notify("Corpus details saved.", "success");
  }

  function resetUrlSourceForm() {
    setUrlSource({ id: "", title: "", url: "", format: "html" });
    setEditingUrlSourceId("");
  }

  function editUrlSource(source: CorpusSource) {
    if (source.type !== "url") return;
    setUrlSource({
      id: source.id,
      title: source.title || "",
      url: source.url || "",
      format: source.format || "html"
    });
    setEditingUrlSourceId(source.id);
  }

  async function saveUrlSource(event: React.FormEvent) {
    event.preventDefault();
    if (!selectedCorpus) throw new Error("Select a corpus first.");
    const sourceId = urlSource.id.trim();
    const existingSource = detail?.sources?.find((source) => source.id === sourceId);
    const shouldUpdate = Boolean(existingSource);
    if (existingSource && existingSource.type !== "url") {
      throw new Error(`Source ${sourceId} already exists as ${existingSource.type}. Remove it before adding a URL source with this ID.`);
    }
    notify(shouldUpdate ? `Updating ${sourceId}...` : "Adding URL source...", "info");
    await adminFetch(
      shouldUpdate
        ? `corpora/${encodeURIComponent(selectedCorpus)}/sources/${encodeURIComponent(sourceId)}`
        : `corpora/${encodeURIComponent(selectedCorpus)}/sources`,
      {
        method: shouldUpdate ? "PUT" : "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          source_id: sourceId,
          type: "url",
          title: urlSource.title.trim() || null,
          url: urlSource.url.trim(),
          format: urlSource.format,
          tags: existingSource?.tags || []
        })
      }
    );
    resetUrlSourceForm();
    await loadCorpusDetail(selectedCorpus);
    notify(shouldUpdate ? "URL source updated." : "URL source added.", "success");
  }

  async function addFileSource(event: React.FormEvent) {
    event.preventDefault();
    if (!selectedCorpus) throw new Error("Select a corpus first.");
    if (!fileSource.file) throw new Error("Choose a file to upload.");
    notify("Uploading object source...", "info");
    const formData = new FormData();
    formData.append("source_id", fileSource.id.trim());
    formData.append("title", fileSource.title.trim());
    formData.append("format", fileSource.format);
    formData.append("tags_json", "[]");
    formData.append("upload", fileSource.file);
    await adminFetch(`corpora/${encodeURIComponent(selectedCorpus)}/sources/upload`, {
      method: "POST",
      body: formData
    });
    setFileSource({ id: "", title: "", format: "pdf", file: null });
    await loadCorpusDetail(selectedCorpus);
    notify("Object source uploaded.", "success");
  }

  async function deleteSource(sourceId: string) {
    if (!selectedCorpus) throw new Error("Select a corpus first.");
    if (!window.confirm(`Remove source ${sourceId} from ${selectedCorpus}?`)) return;
    notify(`Removing ${sourceId}...`, "info");
    await adminFetch(`corpora/${encodeURIComponent(selectedCorpus)}/sources/${encodeURIComponent(sourceId)}`, {
      method: "DELETE"
    });
    await loadCorpusDetail(selectedCorpus);
    notify("Source removed.", "success");
  }

  async function viewJobDetails(jobId: string) {
    const data = await adminFetch<IngestionJob>(`ingestion-jobs/${encodeURIComponent(jobId)}`);
    setJobStatus(data);
  }

  async function cancelJob(jobId: string) {
    if (!window.confirm(`Cancel ingestion job ${jobId}?`)) return;
    notify(`Cancelling ${jobId}...`, "info");
    const data = await adminFetch<IngestionJob>(`ingestion-jobs/${encodeURIComponent(jobId)}/cancel`, { method: "POST" });
    setJobStatus(data);
    if (selectedCorpus) await loadCorpusJobs(selectedCorpus);
    notify("Ingestion job cancelled.", "success");
  }

  async function pollJob(jobId: string) {
    clearPoll();
    const data = await adminFetch<IngestionJob>(`ingestion-jobs/${encodeURIComponent(jobId)}`);
    setJobStatus(data);
    if (["pending", "running", "started"].includes(data.status || "")) {
      const stage = data.stats?.stage ? ` (${String(data.stats.stage).replace(/_/g, " ")})` : "";
      notify(`Ingestion job ${jobId} is ${data.status}${stage}.`, "info");
      pollRef.current = window.setTimeout(() => {
        pollJob(jobId).catch((error) => notify(asErrorMessage(error, "Polling failed."), "error"));
      }, 2000);
      return;
    }
    if (selectedCorpus) {
      await loadCorpusDetail(selectedCorpus);
      await loadCorpusJobs(selectedCorpus);
    }
    if (data.status === "failed") {
      notify(`Ingestion job failed: ${data.error || "No error detail was reported."}`, "error");
    } else if (data.status === "cancelled") {
      notify("Ingestion job was cancelled.", "info");
    } else {
      notify("Ingestion job completed.", "success");
    }
  }

  async function triggerCorpusLoad({ forceReembed = false, wholeCorpus = false } = {}) {
    if (!selectedCorpus) throw new Error("Select a corpus first.");
    const sourceIds = wholeCorpus ? [] : Array.from(selectedSources);
    if (!wholeCorpus && !sourceIds.length) throw new Error("Select at least one resource first.");
    notify("Submitting corpus load...", "info");
    const result = await adminFetch<IngestionJob>(`corpora/${encodeURIComponent(selectedCorpus)}/ingestion-jobs`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        pipeline_id: pipelineId.trim() || null,
        source_ids: wholeCorpus ? null : sourceIds,
        force_reembed: forceReembed,
        configuration: selectedChunkingModel ? { chunking_model: selectedChunkingModel } : {}
      })
    });
    setJobStatus(result);
    if (result.job_id) {
      notify(`Ingestion job ${result.job_id} is ${result.status}.`, "info");
      await pollJob(result.job_id);
    } else {
      notify("Ingestion job was created without a job ID.", "error");
    }
  }

  function toggleSource(sourceId: string, checked: boolean) {
    setSelectedSources((current) => {
      const next = new Set(current);
      if (checked) next.add(sourceId);
      else next.delete(sourceId);
      return next;
    });
  }

  return (
    <div className="view-stack">
      <Card>
        <div className="section-heading">
          <div>
            <h2>Corpus Registry</h2>
            <p>Select a corpus to manage metadata, sources, and ingestion lifecycle.</p>
          </div>
          <PageActions>
            <input
              ref={importRegistryInputRef}
              type="file"
              accept="application/json,.json"
              hidden
              onChange={(event) => {
                const file = event.target.files?.[0] || null;
                importCorpusRegistry(file)
                  .catch((error) => notify(asErrorMessage(error), "error"))
                  .finally(() => {
                    event.target.value = "";
                  });
              }}
            />
            <button className="button secondary" type="button" onClick={() => createCorpus().catch((error) => notify(asErrorMessage(error), "error"))}>
              <Plus size={16} />
              <span>Create Corpus</span>
            </button>
            <button className="button secondary" type="button" onClick={() => importRegistryInputRef.current?.click()}>
              <FileUp size={16} />
              <span>Import Registry</span>
            </button>
            <button className="button secondary" type="button" onClick={() => exportCorpusRegistry().catch((error) => notify(asErrorMessage(error), "error"))} disabled={!selectedCorpus}>
              <Download size={16} />
              <span>Export Registry</span>
            </button>
            <button className="button danger subtle" type="button" onClick={() => deleteCorpus().catch((error) => notify(asErrorMessage(error), "error"))} disabled={!selectedCorpus}>
              <Trash2 size={16} />
              <span>Delete</span>
            </button>
          </PageActions>
        </div>
        <Field label="Selected Corpus">
          <select value={selectedCorpus} onChange={(event) => changeCorpus(event.target.value).catch((error) => notify(asErrorMessage(error), "error"))}>
            {corpora.map((corpusId) => (
              <option key={corpusId} value={corpusId}>
                {corpusId}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Ingestion Policy ID">
          <input value={pipelineId} onChange={(event) => setPipelineId(event.target.value)} placeholder="default" />
        </Field>
      </Card>

      {!selectedCorpus ? (
        <EmptyState title="No corpus selected" body="Create a corpus or load an existing registry to begin." />
      ) : null}

      {form ? (
        <Card>
          <form onSubmit={(event) => saveCorpusDetails(event).catch((error) => notify(asErrorMessage(error), "error"))}>
            <div className="section-heading">
              <div>
                <h2>Registry Details</h2>
                <p>Registry metadata used by ingestion and retrieval services.</p>
              </div>
              <PageActions>
                <button className="button secondary" type="button" onClick={() => (window.location.hash = `corpora/details?corpus=${encodeURIComponent(selectedCorpus)}`)}>
                  <Archive size={16} />
                  <span>Open Corpus Details</span>
                </button>
                <button className="button primary" type="submit">
                  <Save size={16} />
                  <span>Save Details</span>
                </button>
              </PageActions>
            </div>
            <div className="form-grid">
              <Field label="Title">
                <input value={form.title} onChange={(event) => setForm({ ...form, title: event.target.value })} />
              </Field>
              <Field label="Environment">
                <input value={form.environment} onChange={(event) => setForm({ ...form, environment: event.target.value })} />
              </Field>
              <Field label="Tenant ID">
                <input value={form.tenantId} onChange={(event) => setForm({ ...form, tenantId: event.target.value })} />
              </Field>
            </div>
            <Field label="Description">
              <textarea className="small-textarea" value={form.description} onChange={(event) => setForm({ ...form, description: event.target.value })} />
            </Field>
            <div className="form-grid">
              <Field label="Chunking JSON">
                <JsonTextarea minRows={7} value={form.chunkingJson} onChange={(value) => setForm({ ...form, chunkingJson: value })} />
              </Field>
              <Field label="Index JSON">
                <JsonTextarea minRows={7} value={form.indexJson} onChange={(value) => setForm({ ...form, indexJson: value })} />
              </Field>
              <Field label="Metadata JSON">
                <JsonTextarea minRows={7} value={form.metadataJson} onChange={(value) => setForm({ ...form, metadataJson: value })} />
              </Field>
            </div>
          </form>
        </Card>
      ) : null}

      <Card>
        <div className="section-heading">
          <div>
            <h2>Sources</h2>
            <p>Choose individual resources for selective ingestion or run the whole corpus.</p>
          </div>
          <PageActions>
            <button className="button secondary" type="button" onClick={() => triggerCorpusLoad().catch((error) => notify(asErrorMessage(error), "error"))}>
              <Loader2 size={16} />
              <span>Ingest Selected</span>
            </button>
            <button className="button secondary" type="button" onClick={() => triggerCorpusLoad({ forceReembed: true }).catch((error) => notify(asErrorMessage(error), "error"))}>
              <Loader2 size={16} />
              <span>Force Re-embed</span>
            </button>
            <button className="button primary" type="button" onClick={() => triggerCorpusLoad({ wholeCorpus: true }).catch((error) => notify(asErrorMessage(error), "error"))}>
              <Loader2 size={16} />
              <span>Ingest Corpus</span>
            </button>
          </PageActions>
        </div>
        <div className="form-grid compact">
          <Field label="Chunking Model" hint={chunkingModelHint}>
            <select value={selectedChunkingModel} onChange={(event) => setSelectedChunkingModel(event.target.value)} disabled={!chunkingModels.length}>
              {chunkingModels.length ? (
                <>
                  <optgroup label="Allowed by policy">
                    {chunkingModels.map((option) => (
                      <option key={option.id} value={option.id}>
                        {option.id}
                      </option>
                    ))}
                  </optgroup>
                  {blockedChunkingModels.length ? (
                    <optgroup label="Blocked by policy">
                      {blockedChunkingModels.map((option) => (
                        <option key={option.id} value={option.id} disabled>
                          {option.id}
                        </option>
                      ))}
                    </optgroup>
                  ) : null}
                </>
              ) : (
                <>
                  <option value="">No allowed chunking models</option>
                  {blockedChunkingModels.map((option) => (
                    <option key={option.id} value={option.id} disabled>
                      {option.id} blocked by policy
                    </option>
                  ))}
                </>
              )}
            </select>
          </Field>
          <label className="field">
            <span>Model Registry</span>
            <button className="button secondary" type="button" onClick={() => loadChunkingModels(pipelineId, true).catch((error) => notify(asErrorMessage(error, "Unable to load chunking models."), "error"))}>
              <RefreshCw size={16} />
              <span>Reload Models</span>
            </button>
          </label>
        </div>

        {detail?.sources?.length ? (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Select</th>
                  <th>ID</th>
                  <th>Type</th>
                  <th>Format</th>
                  <th>Details</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {detail.sources.map((item) => (
                  <tr key={item.id}>
                    <td>
                      <input type="checkbox" checked={selectedSources.has(item.id)} onChange={(event) => toggleSource(item.id, event.target.checked)} />
                    </td>
                    <td>
                      <code>{item.id}</code>
                    </td>
                    <td>{item.type || "-"}</td>
                    <td>{item.format || "-"}</td>
                    <td>
                      <div className="source-details">
                        {sourceSummary(item).map((line) => (
                          <code key={line}>{line}</code>
                        ))}
                      </div>
                    </td>
                    <td>
                      <div className="row-actions">
                        {item.type === "url" ? (
                          <button className="icon-button" type="button" onClick={() => editUrlSource(item)} title="Edit source">
                            <PencilLine size={16} />
                          </button>
                        ) : null}
                        <button className="icon-button danger" type="button" onClick={() => deleteSource(item.id).catch((error) => notify(asErrorMessage(error), "error"))} title="Remove source">
                          <Trash2 size={16} />
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState title="No sources" body="Add a URL source or upload an object source to populate this corpus." />
        )}
      </Card>

      <div className="two-column">
        <Card>
          <div className="section-heading compact-heading">
            <div>
              <h2>{editingUrlSourceId ? "Edit URL Source" : "Add URL Source"}</h2>
              <p>{editingUrlSourceId ? `Updating ${editingUrlSourceId}.` : "Register an externally hosted document."}</p>
            </div>
            <Link size={18} />
          </div>
          <form className="stacked-form" onSubmit={(event) => saveUrlSource(event).catch((error) => notify(asErrorMessage(error), "error"))}>
            <Field label="Source ID">
              <input value={urlSource.id} disabled={Boolean(editingUrlSourceId)} onChange={(event) => setUrlSource({ ...urlSource, id: event.target.value })} />
            </Field>
            <Field label="Title">
              <input value={urlSource.title} onChange={(event) => setUrlSource({ ...urlSource, title: event.target.value })} />
            </Field>
            <Field label="URL">
              <input type="url" placeholder="https://example.com/doc.html" value={urlSource.url} onChange={(event) => setUrlSource({ ...urlSource, url: event.target.value })} />
            </Field>
            <Field label="Format">
              <select value={urlSource.format} onChange={(event) => setUrlSource({ ...urlSource, format: event.target.value })}>
                {formats.map((format) => (
                  <option key={format} value={format}>
                    {format}
                  </option>
                ))}
              </select>
            </Field>
            <div className="form-actions">
              <button className="button primary" type="submit">{editingUrlSourceId ? "Update URL Source" : "Add URL Source"}</button>
              {editingUrlSourceId ? (
                <button className="button secondary" type="button" onClick={resetUrlSourceForm}>
                  <X size={16} />
                  <span>Cancel</span>
                </button>
              ) : null}
            </div>
          </form>
        </Card>

        <Card>
          <div className="section-heading compact-heading">
            <div>
              <h2>Upload Object Source</h2>
              <p>Upload a local file into object-backed source storage.</p>
            </div>
            <FileUp size={18} />
          </div>
          <form className="stacked-form" onSubmit={(event) => addFileSource(event).catch((error) => notify(asErrorMessage(error), "error"))}>
            <Field label="Source ID">
              <input value={fileSource.id} onChange={(event) => setFileSource({ ...fileSource, id: event.target.value })} />
            </Field>
            <Field label="Title">
              <input value={fileSource.title} onChange={(event) => setFileSource({ ...fileSource, title: event.target.value })} />
            </Field>
            <Field label="File">
              <input type="file" onChange={(event) => setFileSource({ ...fileSource, file: event.target.files?.[0] || null })} />
            </Field>
            <Field label="Format">
              <select value={fileSource.format} onChange={(event) => setFileSource({ ...fileSource, format: event.target.value })}>
                {formats.map((format) => (
                  <option key={format} value={format}>
                    {format}
                  </option>
                ))}
              </select>
            </Field>
            <button className="button primary" type="submit">Upload File</button>
          </form>
        </Card>
      </div>

      <Card>
        <div className="section-heading">
          <div>
            <h2>Ingestion Jobs</h2>
            <p>Track job status and inspect the latest worker progress payload.</p>
          </div>
          <button className="button secondary" type="button" onClick={() => selectedCorpus && loadCorpusJobs(selectedCorpus).catch((error) => notify(asErrorMessage(error), "error"))}>
            Reload Jobs
          </button>
        </div>
        {jobs.length ? (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Job ID</th>
                  <th>Status</th>
                  <th>Created At</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {jobs.map((job) => {
                  const cancellable = job.status === "pending" || job.status === "running";
                  return (
                    <tr key={job.job_id}>
                      <td>
                        <code>{job.job_id}</code>
                      </td>
                      <td>
                        <Badge tone={statusTone(job.status)}>{job.status || "-"}</Badge>
                      </td>
                      <td>{job.created_at || "-"}</td>
                      <td>
                        <div className="row-actions">
                          <button className="button secondary compact" type="button" onClick={() => job.job_id && viewJobDetails(job.job_id).catch((error) => notify(asErrorMessage(error), "error"))}>
                            View
                          </button>
                          {cancellable ? (
                            <button className="button danger subtle compact" type="button" onClick={() => job.job_id && cancelJob(job.job_id).catch((error) => notify(asErrorMessage(error), "error"))}>
                              Cancel
                            </button>
                          ) : null}
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState title="No jobs" body="Submit an ingestion run to create the first job for this corpus." />
        )}
        <div className="job-detail">
          <p>{renderJobSummary(jobStatus)}</p>
          <JsonTextarea value={pretty(jobStatus || {})} onChange={() => undefined} readOnly minRows={10} />
        </div>
      </Card>
    </div>
  );
}
