import { useMemo, useState } from "react";

export type ProviderTelemetryPoint = {
  label: string;
  calls: number;
  tokens: number;
};

export type ProviderTelemetrySeries = {
  provider: string;
  model?: string;
  color: string;
  points: ProviderTelemetryPoint[];
};

type TelemetryRibbonScatterPlotProps = {
  series: ProviderTelemetrySeries[];
};

type HoverPoint = {
  provider: string;
  label: string;
  calls: number;
  tokens: number;
  x: number;
  y: number;
};

const width = 980;
const height = 430;
const originX = 72;
const floorY = 316;
const timeWidth = 660;
const depthX = 48;
const depthY = 26;
const ribbonHalfWidth = 0.22;
const maxHeight = 222;

function formatCompact(value: number) {
  return new Intl.NumberFormat(undefined, {
    notation: value >= 10000 ? "compact" : "standard",
    maximumFractionDigits: value >= 10000 ? 1 : 0
  }).format(value);
}

function svgPoints(points: Array<{ x: number; y: number }>) {
  return points.map((point) => `${point.x.toFixed(1)},${point.y.toFixed(1)}`).join(" ");
}

function pathFrom(points: Array<{ x: number; y: number }>) {
  return points.map((point, index) => `${index === 0 ? "M" : "L"} ${point.x.toFixed(1)} ${point.y.toFixed(1)}`).join(" ");
}

function colorWithAlpha(hex: string, alpha: string) {
  const normalized = hex.replace("#", "");
  const r = parseInt(normalized.slice(0, 2), 16);
  const g = parseInt(normalized.slice(2, 4), 16);
  const b = parseInt(normalized.slice(4, 6), 16);
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

export function TelemetryRibbonScatterPlot({ series }: TelemetryRibbonScatterPlotProps) {
  const [selectedIndex, setSelectedIndex] = useState(() => Math.max(0, (series[0]?.points.length || 1) - 1));
  const [focusedProvider, setFocusedProvider] = useState<string | null>(null);
  const [hoverPoint, setHoverPoint] = useState<HoverPoint | null>(null);

  const plot = useMemo(() => {
    const rows = series.length ? series : [{ provider: "Provider", color: "#00a3ff", points: [{ label: "-", calls: 0, tokens: 0 }] }];
    const pointCount = Math.max(...rows.map((item) => item.points.length), 1);
    const maxTokens = Math.max(1, ...rows.flatMap((item) => item.points.map((point) => point.tokens)));
    const maxCalls = Math.max(1, ...rows.flatMap((item) => item.points.map((point) => point.calls)));
    const zFor = (tokens: number) => (tokens / maxTokens) * maxHeight;
    const xFor = (index: number) => (pointCount === 1 ? timeWidth / 2 : (index / (pointCount - 1)) * timeWidth);
    const project = (timeX: number, lane: number, z: number) => ({
      x: originX + timeX + lane * depthX,
      y: floorY + lane * depthY - z
    });

    const providerShapes = rows.map((item, lane) => {
      const centers = item.points.map((point, index) => ({
        ...project(xFor(index), lane, zFor(point.tokens)),
        point,
        lane,
        timeX: xFor(index)
      }));
      const frontEdge = item.points.map((point, index) => project(xFor(index), lane - ribbonHalfWidth, zFor(point.tokens)));
      const backEdge = item.points.map((point, index) => project(xFor(index), lane + ribbonHalfWidth, zFor(point.tokens))).reverse();
      return {
        ...item,
        lane,
        centers,
        ribbon: [...frontEdge, ...backEdge],
        centerPath: pathFrom(centers)
      };
    });

    const timeLabels = rows[0]?.points.map((point) => point.label) || ["-"];
    const selected = Math.min(selectedIndex, pointCount - 1);
    const selectedTimeX = xFor(selected);
    const maxLane = rows.length - 1 + 0.42;
    const minLane = -0.42;
    const zMax = maxHeight * 1.05;
    const timePlane = [
      project(selectedTimeX, minLane, 0),
      project(selectedTimeX, maxLane, 0),
      project(selectedTimeX, maxLane, zMax),
      project(selectedTimeX, minLane, zMax)
    ];

    return {
      rows,
      pointCount,
      maxTokens,
      maxCalls,
      project,
      xFor,
      zFor,
      providerShapes,
      timeLabels,
      selected,
      selectedTimeX,
      timePlane
    };
  }, [series, selectedIndex]);

  function updateSelection(clientX: number, clientY: number, rect: DOMRect) {
    const localX = ((clientX - rect.left) / rect.width) * width;
    const localY = ((clientY - rect.top) / rect.height) * height;
    const averageLane = Math.max(0, (plot.rows.length - 1) / 2);
    const approximateTimeX = localX - originX - averageLane * depthX;
    const ratio = Math.min(1, Math.max(0, approximateTimeX / timeWidth));
    const nextIndex = Math.round(ratio * Math.max(plot.pointCount - 1, 0));
    setSelectedIndex(nextIndex);

    let nearest: HoverPoint | null = null;
    let nearestProvider: string | null = null;
    let nearestDistance = Number.POSITIVE_INFINITY;
    plot.providerShapes.forEach((shape) => {
      shape.centers.forEach((center) => {
        const dx = center.x - localX;
        const dy = center.y - localY;
        const distance = dx * dx + dy * dy;
        if (distance < nearestDistance) {
          nearestDistance = distance;
          nearestProvider = shape.provider;
          nearest = {
            provider: shape.provider,
            label: center.point.label,
            calls: center.point.calls,
            tokens: center.point.tokens,
            x: center.x,
            y: center.y
          };
        }
      });
    });

    if (nearest && nearestDistance < 1800) {
      setFocusedProvider(nearestProvider);
      setHoverPoint(nearest);
    } else {
      setFocusedProvider(null);
      setHoverPoint(null);
    }
  }

  const selectedLabel = plot.timeLabels[plot.selected] || "-";

  return (
    <div className="telemetry-ribbon-shell">
      <svg
        className="telemetry-ribbon-plot"
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label="3D ribbon scatter telemetry plot showing provider token volume and call bubbles over time"
        onPointerMove={(event) => updateSelection(event.clientX, event.clientY, event.currentTarget.getBoundingClientRect())}
        onPointerLeave={() => {
          setFocusedProvider(null);
          setHoverPoint(null);
        }}
      >
        <defs>
          <linearGradient id="ribbonPanelFill" x1="0" x2="1" y1="0" y2="1">
            <stop offset="0%" stopColor="#0b1d36" />
            <stop offset="100%" stopColor="#0d2a2b" />
          </linearGradient>
          <filter id="bubbleGlow" x="-40%" y="-40%" width="180%" height="180%">
            <feGaussianBlur stdDeviation="2.8" result="blur" />
            <feMerge>
              <feMergeNode in="blur" />
              <feMergeNode in="SourceGraphic" />
            </feMerge>
          </filter>
        </defs>

        <rect x="0" y="0" width={width} height={height} fill="url(#ribbonPanelFill)" />

        <g className="ribbon-floor">
          {plot.providerShapes.map((shape) => {
            const start = plot.project(0, shape.lane, 0);
            const end = plot.project(timeWidth, shape.lane, 0);
            return (
              <g key={shape.provider}>
                <line x1={start.x} x2={end.x} y1={start.y} y2={end.y} />
                <text x={start.x - 12} y={start.y + 4} textAnchor="end">{shape.provider}</text>
              </g>
            );
          })}
          {plot.timeLabels.map((label, index) => {
            const timeX = plot.xFor(index);
            const front = plot.project(timeX, -0.45, 0);
            const back = plot.project(timeX, plot.rows.length - 0.55, 0);
            return (
              <g key={label}>
                <line className="time-grid" x1={front.x} x2={back.x} y1={front.y} y2={back.y} />
                {(index === 0 || index === plot.timeLabels.length - 1 || index % Math.ceil(plot.timeLabels.length / 4) === 0) ? (
                  <text className="time-label" x={front.x} y={front.y + 28} textAnchor="middle">{label}</text>
                ) : null}
              </g>
            );
          })}
        </g>

        <polygon className="time-plane" points={svgPoints(plot.timePlane)} />

        <g>
          {plot.providerShapes.map((shape) => {
            const isFocused = !focusedProvider || focusedProvider === shape.provider;
            return (
              <g key={shape.provider} className={isFocused ? "provider-layer" : "provider-layer dimmed"}>
                <polygon
                  className="provider-ribbon"
                  points={svgPoints(shape.ribbon)}
                  style={{
                    fill: colorWithAlpha(shape.color, focusedProvider === shape.provider ? "0.46" : "0.3"),
                    stroke: shape.color
                  }}
                />
                <path className="provider-ribbon-line" d={shape.centerPath} style={{ stroke: shape.color }} />
                {shape.centers.map((center) => {
                  const radius = 3.5 + Math.sqrt(center.point.calls / plot.maxCalls) * 9.5;
                  const base = plot.project(center.timeX, shape.lane, 0);
                  const selected = center.point.label === selectedLabel;
                  return (
                    <g key={`${shape.provider}-${center.point.label}`}>
                      {selected ? (
                        <line className="bubble-projection" x1={base.x} x2={center.x} y1={base.y} y2={center.y} />
                      ) : null}
                      <circle
                        className={selected ? "ribbon-bubble selected" : "ribbon-bubble"}
                        cx={center.x}
                        cy={center.y}
                        r={selected ? radius + 2 : radius}
                        style={{ fill: shape.color }}
                      />
                    </g>
                  );
                })}
                <text className="provider-end-label" x={shape.centers[shape.centers.length - 1]?.x + 12} y={shape.centers[shape.centers.length - 1]?.y || 0}>
                  {shape.provider}
                </text>
              </g>
            );
          })}
        </g>

        <g className="ribbon-axis">
          <line x1={originX - 34} x2={originX - 34} y1={floorY + 4} y2={floorY - maxHeight - 18} />
          <text x={originX - 34} y={floorY - maxHeight - 28} textAnchor="middle">Tokens</text>
          {[0, 0.5, 1].map((ratio) => {
            const value = Math.round(plot.maxTokens * ratio);
            const y = floorY - plot.zFor(value);
            return <text key={ratio} x={originX - 44} y={y + 4} textAnchor="end">{formatCompact(value)}</text>;
          })}
        </g>
      </svg>

      <div className="telemetry-time-panel">
        <strong>{selectedLabel}</strong>
        {plot.providerShapes.map((shape) => {
          const point = shape.points[plot.selected] || shape.points[shape.points.length - 1];
          const muted = focusedProvider && focusedProvider !== shape.provider;
          return (
            <div key={shape.provider} className={muted ? "time-row muted" : "time-row"}>
              <span><i style={{ background: shape.color }} /> {shape.provider}</span>
              <code>{point.calls.toLocaleString()} calls</code>
              <code>{formatCompact(point.tokens)} tokens</code>
              <code>{formatCompact(Math.round(point.tokens / Math.max(point.calls, 1)))}/call</code>
            </div>
          );
        })}
      </div>

      {hoverPoint ? (
        <div
          className="telemetry-bubble-tooltip"
          style={{
            left: `${Math.min(width - 230, Math.max(24, hoverPoint.x + 16)) / width * 100}%`,
            top: `${Math.max(12, hoverPoint.y - 84) / height * 100}%`
          }}
        >
          <strong>{hoverPoint.provider}</strong>
          <span>{hoverPoint.label}</span>
          <code>{hoverPoint.calls.toLocaleString()} calls</code>
          <code>{hoverPoint.tokens.toLocaleString()} tokens</code>
        </div>
      ) : null}
    </div>
  );
}
