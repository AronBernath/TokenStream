import { Activity, BarChart3, Bot, Database, KeyRound, Network, Shield, Users } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { adminFetch } from "../api";
import { Badge, Card, EmptyState } from "../components/Primitives";
import { TelemetryRibbonScatterPlot } from "../components/TelemetryRibbonScatterPlot";
import type { ProviderTelemetrySeries } from "../components/TelemetryRibbonScatterPlot";
import type { ViewProps } from "../types";
import { asErrorMessage } from "../utils";

type AdminStatus = {
  ok?: boolean;
  users_count?: number;
  providers_count?: number;
  policies_count?: number;
  machine_keys_count?: number;
  mcp_servers_count?: number;
  default_corpus_id?: string;
  retrieval_api_url?: string;
};

const metrics = [
  { key: "users_count", label: "Users", icon: Users },
  { key: "providers_count", label: "Providers", icon: Bot },
  { key: "policies_count", label: "Policies", icon: Shield },
  { key: "machine_keys_count", label: "Machine Keys", icon: KeyRound },
  { key: "mcp_servers_count", label: "Selected MCP Servers", icon: Network }
] as const;

type TelemetryRange = "hour" | "day" | "week" | "month" | "year";

const rangeConfig: Record<TelemetryRange, { label: string; points: number; labelFor: (index: number) => string }> = {
  hour: { label: "Hour", points: 13, labelFor: (index) => `${index * 5}m` },
  day: { label: "Day", points: 13, labelFor: (index) => `${index * 2}h` },
  week: { label: "Week", points: 7, labelFor: (index) => `D${index + 1}` },
  month: { label: "Month", points: 15, labelFor: (index) => `D${index * 2 + 1}` },
  year: {
    label: "Year",
    points: 12,
    labelFor: (index) => ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][index] || `M${index + 1}`
  }
};

const rangeMultipliers: Record<TelemetryRange, number> = {
  hour: 1,
  day: 8,
  week: 28,
  month: 92,
  year: 360
};

const previewProviders = [
  { provider: "OpenAI", model: "openai:gpt-5.4", color: "#00a3ff", tokenWeight: 1.12, callWeight: 1.04 },
  { provider: "DeepSeek", model: "deepseek:deepseek-v4-pro", color: "#16d16f", tokenWeight: 1.0, callWeight: 0.86 },
  { provider: "OpenAI Mini", model: "openai:gpt-5.4-mini", color: "#ff6b00", tokenWeight: 0.82, callWeight: 0.7 },
  { provider: "Mistral", model: "mistral:mistral-large", color: "#f43f5e", tokenWeight: 0.68, callWeight: 0.58 },
  { provider: "Local", model: "ollama:dolphin-llama3:70b", color: "#a855f7", tokenWeight: 0.46, callWeight: 0.42 }
] as const;

function buildPreviewTelemetry(status: AdminStatus | null, range: TelemetryRange): ProviderTelemetrySeries[] {
  const config = rangeConfig[range];
  const policyCount = Math.max(1, status?.policies_count || 1);
  const serverCount = Math.max(1, status?.mcp_servers_count || 1);
  const providerCount = Math.min(Math.max(2, status?.providers_count || 3), previewProviders.length);
  const multiplier = rangeMultipliers[range];

  return previewProviders.slice(0, providerCount).map((provider, providerIndex) => ({
    provider: provider.provider,
    model: provider.model,
    color: provider.color,
    points: Array.from({ length: config.points }, (_, index) => {
      const wave = Math.sin(index * 0.72 + providerIndex * 0.9 + policyCount) + 1.45;
      const pulse = Math.cos(index * 0.48 + providerIndex) + 1.2;
      const ramp = 1 + index / Math.max(config.points - 1, 1);
      const calls = Math.round((7 + wave * 6 * provider.callWeight + pulse * 2) * multiplier * ramp);
      const tokensPerCall = (410 + serverCount * 38 + providerIndex * 170) * provider.tokenWeight;
      return {
        label: config.labelFor(index),
        calls,
        tokens: Math.round(calls * tokensPerCall * (0.82 + wave * 0.12))
      };
    })
  }));
}

function sumTelemetry(data: ProviderTelemetrySeries[], key: "calls" | "tokens") {
  return data.reduce((total, provider) => total + provider.points.reduce((providerTotal, point) => providerTotal + point[key], 0), 0);
}

function formatCompact(value: number) {
  return new Intl.NumberFormat(undefined, {
    notation: value >= 10000 ? "compact" : "standard",
    maximumFractionDigits: value >= 10000 ? 1 : 0
  }).format(value);
}

function topCallRows(data: ProviderTelemetrySeries[]) {
  return data
    .map((item) => {
      const calls = item.points.reduce((total, point) => total + point.calls, 0);
      const tokens = item.points.reduce((total, point) => total + point.tokens, 0);
      return {
        model: item.model || item.provider,
        provider: item.provider,
        color: item.color,
        calls,
        tokens,
        tokensPerCall: Math.round(tokens / Math.max(calls, 1))
      };
    })
    .sort((left, right) => right.calls - left.calls);
}

export function DashboardView({ refreshNonce, notify }: ViewProps) {
  const [status, setStatus] = useState<AdminStatus | null>(null);
  const [loading, setLoading] = useState(false);
  const [telemetryRange, setTelemetryRange] = useState<TelemetryRange>("day");

  const telemetry = useMemo(() => buildPreviewTelemetry(status, telemetryRange), [status, telemetryRange]);
  const topCalls = useMemo(() => topCallRows(telemetry), [telemetry]);
  const totalCalls = sumTelemetry(telemetry, "calls");
  const totalTokens = sumTelemetry(telemetry, "tokens");
  const maxTopCalls = Math.max(1, ...topCalls.map((row) => row.calls));

  useEffect(() => {
    setLoading(true);
    notify("Loading dashboard...", "info");
    adminFetch<AdminStatus>("status")
      .then((data) => {
        setStatus(data);
        notify("Dashboard loaded.", "success");
      })
      .catch((error) => notify(asErrorMessage(error), "error"))
      .finally(() => setLoading(false));
  }, [notify, refreshNonce]);

  if (!status && !loading) {
    return <EmptyState title="No status loaded" body="Use refresh to request the runtime registry status." />;
  }

  return (
    <div className="view-stack">
      <div className="dashboard-hero-grid">
        <Card className="telemetry-card">
          <div className="telemetry-panel-header">
            <div>
              <div className="eyebrow">Usage Telemetry Preview</div>
              <h2>Provider Ribbon Scatter</h2>
              <p>Fixed provider lanes, token-height ribbons, and call-volume bubbles. Hover to isolate a provider or compare the selected time bucket.</p>
            </div>
            <div className="range-switch" aria-label="Telemetry range">
              {(Object.keys(rangeConfig) as TelemetryRange[]).map((range) => (
                <button
                  key={range}
                  className={telemetryRange === range ? "active" : ""}
                  type="button"
                  onClick={() => setTelemetryRange(range)}
                >
                  {rangeConfig[range].label}
                </button>
              ))}
            </div>
          </div>
          <div className="telemetry-plot-wrap">
            <TelemetryRibbonScatterPlot series={telemetry} />
            <div className="telemetry-legend">
              <span><i className="legend-dot tokens" /> Ribbon height: tokens</span>
              <span><i className="legend-dot calls" /> Bubble area: calls</span>
              <span><i className="legend-plane" /> Selected time</span>
            </div>
          </div>
          <div className="telemetry-stat-row">
            <div>
              <span>Total Calls</span>
              <strong>{totalCalls.toLocaleString()}</strong>
            </div>
            <div>
              <span>Total Tokens</span>
              <strong>{totalTokens.toLocaleString()}</strong>
            </div>
            <div>
              <span>Provider Lanes</span>
              <strong>{telemetry.length}</strong>
            </div>
          </div>
        </Card>

        <Card className="top-calls-card top-calls-card-horizontal">
          <div className="top-calls-summary">
            <div className="section-heading compact-heading">
              <div>
                <h2>Top Calls</h2>
                <p>Models ranked by call volume for the selected preview range.</p>
              </div>
              <BarChart3 size={20} />
            </div>
            <Badge tone="warn">Preview data</Badge>
          </div>
          <div className="top-calls-list">
            {topCalls.map((row, index) => (
              <div className="top-call-row" key={row.model}>
                <div className="top-call-heading">
                  <span>
                    <i style={{ background: row.color }} />
                    <code>{row.model}</code>
                  </span>
                  <strong>{row.calls.toLocaleString()}</strong>
                </div>
                <div className="top-call-meter" aria-hidden="true">
                  <span style={{ width: `${Math.max(8, (row.calls / maxTopCalls) * 100)}%`, background: row.color, color: row.color }} />
                </div>
                <div className="top-call-meta">
                  <span>#{index + 1} {row.provider}</span>
                  <code>{formatCompact(row.tokens)} tokens</code>
                  <code>{formatCompact(row.tokensPerCall)}/call</code>
                </div>
              </div>
            ))}
          </div>
        </Card>
      </div>

      <div className="metric-grid">
        <Card className="metric-card health-card">
          <div className="metric-icon">
            <Activity size={20} />
          </div>
          <span>System</span>
          <strong>{status?.ok ? "OK" : loading ? "Loading" : "Error"}</strong>
          <Badge tone={status?.ok ? "good" : "bad"}>{status?.ok ? "Healthy" : "Needs attention"}</Badge>
        </Card>
        {metrics.map((metric) => {
          const Icon = metric.icon;
          return (
            <Card className="metric-card" key={metric.key}>
              <div className="metric-icon">
                <Icon size={20} />
              </div>
              <span>{metric.label}</span>
              <strong>{String(status?.[metric.key] ?? "-")}</strong>
            </Card>
          );
        })}
      </div>

      <Card>
        <div className="section-heading">
          <div>
            <h2>Retrieval Routing</h2>
            <p>Current defaults exported to orchestrator-api and retrieval clients.</p>
          </div>
          <Database size={20} />
        </div>
        <div className="definition-list">
          <div>
            <span>Default Corpus</span>
            <code>{status?.default_corpus_id || "-"}</code>
          </div>
          <div>
            <span>Retrieval API URL</span>
            <code>{status?.retrieval_api_url || "-"}</code>
          </div>
        </div>
      </Card>
    </div>
  );
}
