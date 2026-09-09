import {
  ArrowRight,
  Bot,
  Box,
  CircleGauge,
  Database,
  HardDrive,
  MessageSquare,
  Play,
  RotateCcw,
  Server,
  Square,
} from "lucide-react";
import { formatBytes, post } from "../lib/api";
import { Badge, Empty, Meter } from "../components/ui";
import type { EngineStatus, Metrics, Model } from "../types";

export default function Dashboard({
  engine,
  models,
  metrics,
  refresh,
  toast,
  navigate,
}: {
  engine: EngineStatus;
  models: Model[];
  metrics: Metrics | null;
  refresh: () => void;
  toast: (s: string, b?: boolean) => void;
  navigate: (s: string) => void;
}) {
  const ready =
      (engine.state === "ready" || engine.state === "external") &&
      engine.health?.status === "ok",
    external = engine.mode === "external",
    gpu = metrics?.system.gpus[0],
    runtime = metrics?.runtime || {},
    tps = runtime.throughput?.decode_tps;
  const action = async (kind: "unload" | "restart") => {
    try {
      await post(`/api/engine/${kind}`);
      toast(kind === "unload" ? "Model unloaded" : "Engine restarting");
      refresh();
    } catch (e: any) {
      toast(e.message, true);
    }
  };
  return (
    <div className="page dashboard">
      <div className="page-heading">
        <div>
          <div className="eyebrow">LOCAL INFERENCE / LIVE</div>
          <h1>Good evening, operator.</h1>
          <p>Everything you need to run FreeToken, without the terminal.</p>
        </div>
        <Badge
          tone={ready ? "good" : engine.state === "failed" ? "bad" : "warn"}
        >
          {engine.state}
        </Badge>
      </div>
      {external ? (
        <section className="hero-runtime external-runtime">
          <div className="hero-main">
            <div className="status-rings"><span /><span /><Bot /></div>
            <div><small>EXTERNAL RUNTIME</small><h2>{engine.model || "Waiting for FreeToken"}</h2>
              <div className="runtime-meta"><span><i className={ready ? "green" : "amber"} />{ready ? "Connected and accepting requests" : "Connection unavailable"}</span><span>External mode</span></div>
            </div>
          </div>
          <div className="hero-actions"><button className="secondary" onClick={() => navigate("chat")} disabled={!ready}><MessageSquare size={16} />Open chat</button></div>
          {engine.error && <div className="inline-error">{engine.error}</div>}
          <div className="external-note">Lifecycle and model storage are controlled by the external FreeToken host.</div>
        </section>
      ) : models.length === 0 ? (
        <Empty icon={<Box />} title="Your library is waiting">
          No complete models were found in the configured storage directory.
          <button className="primary" onClick={() => navigate("models")}>
            Browse verified models <ArrowRight size={16} />
          </button>
        </Empty>
      ) : (
        <section className="hero-runtime">
          <div className="hero-main">
            <div className="status-rings">
              <span />
              <span />
              <Bot />
            </div>
            <div>
              <small>ACTIVE RUNTIME</small>
              <h2>{engine.model || "No model loaded"}</h2>
              <div className="runtime-meta">
                <span>
                  <i className={ready ? "green" : "amber"} />
                  {ready ? "Accepting requests" : engine.state}
                </span>
                {engine.pid && <span>PID {engine.pid}</span>}
                <span>{engine.mode} mode</span>
              </div>
            </div>
          </div>
          <div className="hero-actions">
            {ready && engine.owned ? (
              <>
                <button className="secondary" onClick={() => action("restart")}>
                  <RotateCcw size={16} />
                  Restart
                </button>
                <button
                  className="danger-quiet"
                  onClick={() => action("unload")}
                >
                  <Square size={15} />
                  Unload
                </button>
              </>
            ) : (
              <button className="primary" onClick={() => navigate("models")}>
                <Play size={16} />
                Load model
              </button>
            )}
            <button
              className="secondary"
              onClick={() => navigate("chat")}
              disabled={!ready}
            >
              <MessageSquare size={16} />
              Open chat
            </button>
          </div>
          {engine.error && <div className="inline-error">{engine.error}</div>}
          {["starting", "loading"].includes(engine.state) && (
            <div className="load-strip">
              <span>
                <b>{engine.phase?.replace("_", " ") || "Starting FreeToken"}</b>
                <em>
                  {engine.progress?.percent != null
                    ? `${engine.progress.percent}%`
                    : "Waiting for runtime"}
                </em>
              </span>
              <Meter value={engine.progress?.percent || 8} />
            </div>
          )}
        </section>
      )}
      <div className="metric-grid">
        <Metric
          icon={<CircleGauge />}
          label="Decode speed"
          value={tps?.toFixed(1) ?? "—"}
          unit="tok/s"
          detail={`${runtime.throughput?.prefill_tps ?? "—"} prefill tok/s`}
        />
        <Metric
          icon={<Database />}
          label="GPU memory"
          value={gpu ? formatBytes(gpu.memoryUsedBytes) : "—"}
          unit={gpu ? `/ ${formatBytes(gpu.memoryTotalBytes)}` : ""}
          percent={gpu?.memoryPercent}
          detail={
            gpu
              ? "Engine GPU; live utilization unavailable"
              : "NVML unavailable"
          }
        />
        <Metric
          icon={<Server />}
          label="WebUI host memory"
          value={metrics ? formatBytes(metrics.system.ram.usedBytes) : "—"}
          unit={
            metrics ? `/ ${formatBytes(metrics.system.ram.totalBytes)}` : ""
          }
          percent={metrics?.system.ram.percent}
          detail={
            metrics
              ? `${formatBytes(metrics.system.ram.availableBytes)} available`
              : "Reading system"
          }
        />
        <Metric
          icon={<HardDrive />}
          label="Model storage"
          value={metrics ? formatBytes(metrics.system.storage.usedBytes) : "—"}
          unit="used"
          percent={metrics?.system.storage.percent}
          detail={
            metrics
              ? `${formatBytes(metrics.system.storage.freeBytes)} free`
              : "Reading disk"
          }
        />
      </div>
      <section className="split-section">
        <div className="panel quick">
          <header>
            <div>
              <small>QUICK LAUNCH</small>
              <h3>Model library</h3>
            </div>
            <button className="text-button" onClick={() => navigate("models")}>
              View all <ArrowRight size={14} />
            </button>
          </header>
          {models.slice(0, 3).map((m) => (
            <button
              className="quick-model"
              key={m.id}
              onClick={() => navigate("models")}
            >
              <span className="model-glyph">
                {m.name.slice(0, 2).toUpperCase()}
              </span>
              <div>
                <strong>{m.name}</strong>
                <small>
                  {m.parameterCount || m.architecture} ·{" "}
                  {m.quantization || "native"}
                </small>
              </div>
              <Badge tone={m.status === "running" ? "good" : "neutral"}>
                {m.status}
              </Badge>
            </button>
          ))}
        </div>
        <div className="panel requests">
          <header>
            <div>
              <small>REQUEST QUEUE</small>
              <h3>Runtime pulse</h3>
            </div>
            <span className="live-dot">LIVE</span>
          </header>
          <div className="request-numbers">
            <div>
              <b>{runtime.requests?.active ?? "—"}</b>
              <span>ACTIVE</span>
            </div>
            <div>
              <b>{runtime.requests?.completed ?? "—"}</b>
              <span>COMPLETED</span>
            </div>
            <div>
              <b>
                {runtime.requests?.p95_ms ?? "—"}
                <em>ms</em>
              </b>
              <span>P95 LATENCY</span>
            </div>
          </div>
        </div>
      </section>
    </div>
  );
}

function Metric({
  icon,
  label,
  value,
  unit,
  detail,
  percent,
}: {
  icon: any;
  label: string;
  value: string;
  unit: string;
  detail: string;
  percent?: number;
}) {
  return (
    <article className="metric-card">
      <header>
        <span>{icon}</span>
        <small>{label}</small>
        {percent != null && <em>{percent}%</em>}
      </header>
      <div className="metric-value">
        {value}
        <small>{unit}</small>
      </div>
      {percent != null && (
        <Meter value={percent} tone={percent > 90 ? "orange" : "accent"} />
      )}
      <p>{detail}</p>
    </article>
  );
}
