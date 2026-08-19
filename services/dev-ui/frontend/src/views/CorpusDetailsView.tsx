import { ArrowLeft, Database, FileSearch, Layers3, Play, RefreshCw } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { adminFetch } from "../api";
import { Badge, Card, EmptyState, Field, JsonTextarea, PageActions } from "../components/Primitives";
import type { CorpusDetail, CorpusSource, IngestionJob, ViewProps } from "../types";
import { asErrorMessage, pretty, statusTone } from "../utils";

type CorpusCatalog = {
  corpora: string[];
};

function currentCorpusFromHash() {
  const hash = window.location.hash || "";
  const query = hash.includes("?") ? hash.slice(hash.indexOf("?") + 1) : "";
  return new URLSearchParams(query).get("corpus") || "";
}

function valueFromStats(stats: Record<string, unknown> | undefined, key: string) {
  const value = stats?.[key];
  return typeof value === "number" || typeof value === "string" ? String(value) : "-";
}

function nestedValue(data: unknown, path: string[]) {
  let current = data;
  for (const key of path) {
    if (!current || typeof current !== "object" || !(key in current)) return undefined;
    current = (current as Record<string, unknown>)[key];
  }
  return current;
}

function displayValue(value: unknown) {
  return typeof value === "number" || typeof value === "string" ? String(value) : "-";
}

function locationForSource(source: CorpusSource) {
  return source.type === "url" ? source.url || "-" : source.object_uri || "-";
}

function compactDate(value?: string | null) {
  if (!value) return "-";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function latestCompletedJob(jobs: IngestionJob[]) {
  return jobs.find((job) => job.status === "completed") || null;
}

function chunkingModelFromJob(job: IngestionJob | null) {
  const config = job?.request?.configuration;
  if (config && typeof config === "object" && "chunking_model" in config) {
    const model = (config as Record<string, unknown>).chunking_model;
    return typeof model === "string" && model ? model : "-";
  }
  return "-";
}

function sourceHashCount(sources: CorpusSource[]) {
  return sources.filter((source) => Boolean(source.content_hash)).length;
}

export function CorpusDetailsView({ refreshNonce, notify }: ViewProps) {
  const [corpora, setCorpora] = useState<string[]>([]);
  const [selectedCorpus, setSelectedCorpus] = useState(currentCorpusFromHash);
  const [detail, setDetail] = useState<CorpusDetail | null>(null);
  const [jobs, setJobs] = useState<IngestionJob[]>([]);
  const [loading, setLoading] = useState(false);
  const [dryRunSourceId, setDryRunSourceId] = useState("");
  const [dryRunLoading, setDryRunLoading] = useState(false);
  const [dryRunResult, setDryRunResult] = useState<Record<string, unknown> | null>(null);
  const selectedCorpusRef = useRef(selectedCorpus);

  useEffect(() => {
    selectedCorpusRef.current = selectedCorpus;
  }, [selectedCorpus]);

  const corpusJobs = useMemo(() => jobs.filter((job) => job.corpus_id === selectedCorpus), [jobs, selectedCorpus]);
  const latestJob = corpusJobs[0] || null;
  const completedJob = latestCompletedJob(corpusJobs);
  const artifactJob = completedJob || latestJob;
  const artifactStats = artifactJob?.stats || {};
  const sources = detail?.sources || [];
  const qdrantPoints = nestedValue(artifactStats, ["indexes", "qdrant", "points_upserted"]) ?? artifactStats.qdrant_points;
  const sqliteRows = nestedValue(artifactStats, ["indexes", "lexical", "rows_written"]) ?? artifactStats.sqlite_rows;
  const chunkPreviewCount = Array.isArray(artifactStats.chunk_preview) ? artifactStats.chunk_preview.length : 0;

  const loadArtifacts = useCallback(
    async (corpusId?: string) => {
      setLoading(true);
      try {
        const catalog = await adminFetch<CorpusCatalog>("corpora");
        const nextCorpora = catalog.corpora || [];
        setCorpora(nextCorpora);
        const nextCorpus = corpusId || selectedCorpusRef.current || nextCorpora[0] || "";
        setSelectedCorpus(nextCorpus);
        if (!nextCorpus) {
          setDetail(null);
          setJobs([]);
          return;
        }
        const [nextDetail, nextJobs] = await Promise.all([
          adminFetch<CorpusDetail>(`corpora/${encodeURIComponent(nextCorpus)}`),
          adminFetch<IngestionJob[]>("ingestion-jobs")
        ]);
        setDetail(nextDetail);
        setJobs(nextJobs);
        const nextSources = nextDetail.sources || [];
        setDryRunSourceId((current) => (!current && nextSources.length ? nextSources[0].id : current));
      } catch (error) {
        notify(asErrorMessage(error, "Unable to load corpus details."), "error");
      } finally {
        setLoading(false);
      }
    },
    [notify]
  );

  useEffect(() => {
    loadArtifacts().catch((error) => notify(asErrorMessage(error, "Unable to load corpus details."), "error"));
  }, [loadArtifacts, notify, refreshNonce]);

  function chooseCorpus(corpusId: string) {
    window.location.hash = `corpora/details?corpus=${encodeURIComponent(corpusId)}`;
    setDryRunResult(null);
    setDryRunSourceId("");
    loadArtifacts(corpusId).catch((error) => notify(asErrorMessage(error, "Unable to load corpus details."), "error"));
  }

  async function runChunkingDryRun() {
    if (!selectedCorpus) throw new Error("Select a corpus first.");
    setDryRunLoading(true);
    notify("Running chunking dry-run...", "info");
    try {
      const chunkingModel = chunkingModelFromJob(artifactJob);
      const result = await adminFetch<Record<string, unknown>>(`corpora/${encodeURIComponent(selectedCorpus)}/chunking-dry-run`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          source_id: dryRunSourceId || null,
          chunking_model: chunkingModel !== "-" ? chunkingModel : null,
          max_preview_chunks: 5
        })
      });
      setDryRunResult(result);
      notify("Chunking dry-run completed.", "success");
    } catch (error) {
      notify(asErrorMessage(error, "Chunking dry-run failed."), "error");
    } finally {
      setDryRunLoading(false);
    }
  }

  return (
    <div className="view-stack">
      <Card>
        <div className="section-heading">
          <div>
            <h2>Corpus Details</h2>
            <p>Read-only ingestion quality, index write counts, and source signals collected from dry-runs and job history.</p>
          </div>
          <PageActions>
            <button className="button secondary" type="button" onClick={() => (window.location.hash = "corpora")}>
              <ArrowLeft size={16} />
              <span>Back to Corpora</span>
            </button>
            <button className="button secondary" type="button" onClick={() => loadArtifacts().catch((error) => notify(asErrorMessage(error), "error"))} disabled={loading}>
              <RefreshCw size={16} />
              <span>{loading ? "Loading" : "Refresh"}</span>
            </button>
          </PageActions>
        </div>
        <Field label="Corpus">
          <select value={selectedCorpus} onChange={(event) => chooseCorpus(event.target.value)} disabled={!corpora.length}>
            {corpora.length ? corpora.map((corpus) => <option key={corpus} value={corpus}>{corpus}</option>) : <option value="">No corpora</option>}
          </select>
        </Field>
      </Card>

      {!selectedCorpus ? <EmptyState title="No corpus selected" body="Create a corpus before viewing details." /> : null}

      {selectedCorpus ? (
        <>
          <div className="metric-grid artifact-metrics">
            <Card className="metric-card">
              <span>Sources</span>
              <strong>{sources.length}</strong>
            </Card>
            <Card className="metric-card">
              <span>Hashed Sources</span>
              <strong>{sourceHashCount(sources)}</strong>
            </Card>
            <Card className="metric-card">
              <span>Chunks</span>
              <strong>{valueFromStats(artifactStats, "chunks_produced")}</strong>
            </Card>
            <Card className="metric-card">
              <span>Qdrant Points</span>
              <strong>{displayValue(qdrantPoints)}</strong>
            </Card>
            <Card className="metric-card">
              <span>SQLite Rows</span>
              <strong>{displayValue(sqliteRows)}</strong>
            </Card>
            <Card className="metric-card">
              <span>Latest Job</span>
              <strong>{latestJob?.status || "-"}</strong>
            </Card>
          </div>

          <div className="two-column">
            <Card>
              <div className="section-heading compact-heading">
                <div>
                  <h2>Ingestion Summary</h2>
                  <p>Generated counts reported by the ingestion worker.</p>
                </div>
                {artifactJob?.status ? <Badge tone={statusTone(artifactJob.status)}>{artifactJob.status}</Badge> : null}
              </div>
              {artifactJob ? (
                <div className="definition-list">
                  <div><span>Job ID</span><code>{artifactJob.job_id}</code></div>
                  <div><span>Updated</span><strong>{compactDate(artifactJob.updated_at || artifactJob.created_at)}</strong></div>
                  <div><span>Chunking Model</span><strong>{chunkingModelFromJob(artifactJob)}</strong></div>
                  <div><span>Sources Processed</span><strong>{valueFromStats(artifactStats, "sources_processed")}</strong></div>
                  <div><span>Sources Failed</span><strong>{valueFromStats(artifactStats, "sources_failed")}</strong></div>
                  <div><span>Skipped Unchanged</span><strong>{valueFromStats(artifactStats, "sources_skipped_unchanged")}</strong></div>
                </div>
              ) : (
                <EmptyState title="No ingestion result" body="Run ingestion for this corpus to populate summary counts." />
              )}
            </Card>

            <Card>
              <div className="section-heading compact-heading">
                <div>
                  <h2>Storage Surfaces</h2>
                  <p>What this page can infer from existing management APIs.</p>
                </div>
                <Database size={20} />
              </div>
              <div className="definition-list">
                <div><span>Registry</span><strong>{detail ? "available" : "not loaded"}</strong></div>
                <div><span>Job Stats</span><strong>{artifactJob?.stats ? "available" : "not available"}</strong></div>
                <div><span>Chunk Preview</span><strong>{chunkPreviewCount ? `${chunkPreviewCount} preview chunk${chunkPreviewCount === 1 ? "" : "s"}` : "not reported"}</strong></div>
                <div><span>Qdrant Collection</span><strong>{displayValue(qdrantPoints)} point{qdrantPoints === 1 ? "" : "s"} upserted</strong></div>
                <div><span>Lexical SQLite</span><strong>{displayValue(sqliteRows)} row{sqliteRows === 1 ? "" : "s"} written</strong></div>
              </div>
            </Card>
          </div>

          <Card>
            <div className="section-heading compact-heading">
              <div>
                  <h2>Source State</h2>
                <p>Registered source state and persisted hashes when available.</p>
              </div>
              <FileSearch size={20} />
            </div>
            {sources.length ? (
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>ID</th>
                      <th>Format</th>
                      <th>Location</th>
                      <th>Content Hash</th>
                      <th>Metadata</th>
                    </tr>
                  </thead>
                  <tbody>
                    {sources.map((source) => (
                      <tr key={source.id}>
                        <td><code>{source.id}</code></td>
                        <td>{source.format || "-"}</td>
                        <td><code>{locationForSource(source)}</code></td>
                        <td>{source.content_hash ? <code>{source.content_hash}</code> : "-"}</td>
                        <td>{source.metadata && Object.keys(source.metadata).length ? <code>{JSON.stringify(source.metadata)}</code> : "-"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <EmptyState title="No sources" body="Add sources to this corpus before ingestion." />
            )}
          </Card>

          <Card>
            <div className="section-heading compact-heading">
              <div>
                <h2>Chunking Dry-Run</h2>
                <p>Fetches one source, parses it with the active core parser, chunks it, and returns a JSON preview without embedding or writing indexes.</p>
              </div>
              <PageActions>
                <button className="button secondary" type="button" onClick={() => runChunkingDryRun().catch((error) => notify(asErrorMessage(error), "error"))} disabled={dryRunLoading || !sources.length}>
                  <Play size={16} />
                  <span>{dryRunLoading ? "Running" : "Run Dry-Run"}</span>
                </button>
              </PageActions>
            </div>
            <Field label="Dry-Run Source">
              <select value={dryRunSourceId} onChange={(event) => setDryRunSourceId(event.target.value)} disabled={!sources.length || dryRunLoading}>
                {sources.length ? sources.map((source) => <option key={source.id} value={source.id}>{source.id}</option>) : <option value="">No sources</option>}
              </select>
            </Field>
            <JsonTextarea
              readOnly
              minRows={14}
              value={pretty(dryRunResult || { status: "not_run", hint: "Select a source and run a chunking dry-run." })}
              onChange={() => undefined}
            />
          </Card>

          <div className="two-column">
            <Card>
              <div className="section-heading compact-heading">
                <div>
                  <h2>Latest Job Stats</h2>
                  <p>Raw stats persisted with the ingestion job.</p>
                </div>
                <Layers3 size={20} />
              </div>
              <JsonTextarea readOnly minRows={12} value={pretty(artifactStats)} onChange={() => undefined} />
            </Card>
            <Card>
              <div className="section-heading compact-heading">
                <div>
                  <h2>Corpus Config Snapshot</h2>
                  <p>Editable registry JSON shown here read-only for comparison.</p>
                </div>
              </div>
              <JsonTextarea
                readOnly
                minRows={12}
                value={pretty({
                  chunking: detail?.chunking || {},
                  index: detail?.index || {},
                  metadata: detail?.metadata || {}
                })}
                onChange={() => undefined}
              />
            </Card>
          </div>

          <Card>
            <div className="section-heading compact-heading">
              <div>
                <h2>Job History</h2>
                <p>Recent ingestion jobs for the selected corpus.</p>
              </div>
            </div>
            {corpusJobs.length ? (
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Status</th>
                      <th>Job ID</th>
                      <th>Updated</th>
                      <th>Chunks</th>
                      <th>Qdrant</th>
                      <th>Error</th>
                    </tr>
                  </thead>
                  <tbody>
                    {corpusJobs.map((job) => (
                      <tr key={job.job_id}>
                        <td><Badge tone={statusTone(job.status || "")}>{job.status || "unknown"}</Badge></td>
                        <td><code>{job.job_id}</code></td>
                        <td>{compactDate(job.updated_at || job.created_at)}</td>
                        <td>{valueFromStats(job.stats, "chunks_produced")}</td>
                        <td>{valueFromStats(job.stats, "qdrant_points")}</td>
                        <td>{job.error ? <code>{job.error}</code> : "-"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <EmptyState title="No jobs" body="Run ingestion for this corpus to create job history." />
            )}
          </Card>
        </>
      ) : null}
    </div>
  );
}
