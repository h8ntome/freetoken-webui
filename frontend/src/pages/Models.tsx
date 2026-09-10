import { useEffect, useState } from "react";
import { ChevronDown, Download, ExternalLink, FolderSearch, HardDrive, History, Info, LoaderCircle, MoreHorizontal, Play, RefreshCw, Search, ShieldCheck, Trash2, XCircle } from "lucide-react";
import { api, del, formatBytes, post } from "../lib/api";
import { Badge, Meter, Modal } from "../components/ui";
import type { EngineStatus, Model } from "../types";

export default function Models({ engine, models, refresh, toast }: { engine: EngineStatus; models: Model[]; refresh: () => void; toast: (s: string, b?: boolean) => void }) {
  const managed = engine.mode === "managed";
  const lifecycle = managed && engine.owned;
  const [tab, setTab] = useState<"library" | "discover">("library");
  const [query, setQuery] = useState("");
  const [catalog, setCatalog] = useState<any[]>([]);
  const [showAll, setShowAll] = useState(false);
  const [history, setHistory] = useState<any[]>([]);
  const [searching, setSearching] = useState(false);
  const [job, setJob] = useState<any>(null);
  const [confirm, setConfirm] = useState<Model | null>(null);
  const [advanced, setAdvanced] = useState<Model | null>(null);

  useEffect(() => {
    if (managed) {
      api<any>("/api/models/catalog").then((value) => setCatalog(value.items)).catch(e => toast(e.message, true));
      api<any>("/api/jobs").then(value => { setHistory(value.items); setJob(value.items[0] || null); }).catch(e => toast(e.message, true));
    }
  }, [managed]);
  useEffect(() => {
    if (!job || !["queued", "running", "cancelling"].includes(job.state)) return;
    const id = setInterval(() => api<any>(`/api/jobs/${job.id}`).then((value) => {
      setJob(value);
      setHistory(items => items.map(item => item.id === value.id ? value : item));
      if (value.state === "completed") { toast("Model download complete"); refresh(); }
    }).catch(e => toast(e.message, true)), 1000);
    return () => clearInterval(id);
  }, [job, refresh, toast]);

  const load = async (model: Model, options = {}) => {
    if (!lifecycle) return;
    try {
      await post(`/api/models/${encodeURIComponent(model.id)}/load`, { options, switch: engine.state === "ready" });
      toast(`Loading ${model.name}`); setAdvanced(null); refresh();
    } catch (error: any) { toast(error.message, true); }
  };
  const remove = async (model: Model) => {
    if (model.status === "running") { toast("Unload the model before deleting it", true); return; }
    try {
      await del(`/api/models/${encodeURIComponent(model.id)}`);
      toast(`${model.name} deleted`); setConfirm(null); refresh();
    } catch (error: any) { toast(error.message, true); }
  };
  const discover = async () => {
    setSearching(true);
    try {
      const value = await api<any>(`/api/models/search?query=${encodeURIComponent(query)}&show_all=${showAll}`);
      setCatalog(value.items);
    } catch (error: any) { toast(error.message, true); }
    finally { setSearching(false); }
  };
  const download = async (repo: string, revision = "main") => {
    try {
      const value = await post<any>("/api/models/download", { repoId: repo, revision });
      setJob(value); setHistory(items => [value, ...items]); toast("Download started");
    } catch (error: any) { toast(error.message, true); }
  };

  if (!managed) return <div className="page models-page">
    <div className="page-heading"><div><div className="eyebrow">EXTERNAL RUNTIME / CONNECTION ONLY</div><h1>Models</h1><p>The connected FreeToken server owns its models and lifecycle.</p></div><Badge tone="warn">External mode</Badge></div>
    <section className="mode-boundary"><span><Info /></span><div><small>MANAGEMENT BOUNDARY</small><h2>Model controls live on the external server</h2><p>FreeToken WebUI can chat with and monitor the active external model, but it cannot safely download, load, unload, switch, or delete models on a separately managed host.</p><a href="#settings">Review connection settings</a></div></section>
  </div>;

  return <div className="page models-page">
    <div className="page-heading"><div><div className="eyebrow">CHECKPOINTS / LOCAL STORAGE</div><h1>Models</h1><p>Discover upstream-listed checkpoints, then download and launch them in place.</p></div><button className="secondary" onClick={async () => { try { await post("/api/models/rescan"); refresh(); toast("Library rescanned"); } catch(e: any) { toast(e.message, true); } }}><RefreshCw size={16} />Rescan</button></div>
    {!lifecycle && <div className="setting-warning"><Info />Another service owns the inference port. Local downloads remain available, but load controls are disabled to avoid replacing an unowned process.</div>}
    <div className="tabs"><button className={tab === "library" ? "active" : ""} onClick={() => setTab("library")}>Library <em>{models.length}</em></button><button className={tab === "discover" ? "active" : ""} onClick={() => setTab("discover")}>Discover</button></div>
    {job && ["queued", "running", "cancelling", "failed", "cancelled"].includes(job.state) && <section className="download-job"><div><span className="download-icon"><Download /></span><div><small>{job.progress?.file || job.payload?.repoId}</small><strong>{job.state === "failed" ? job.error : `${job.state}: ${job.payload?.repoId}`}</strong></div></div><div className="job-progress"><span>{formatBytes(job.progress?.downloadedBytes)} / {formatBytes(job.progress?.totalBytes)}<b>{job.progress?.percent ?? 0}%</b></span><Meter value={job.progress?.percent ?? 0} /><small>{job.progress?.speedBytesPerSecond ? `${formatBytes(job.progress.speedBytesPerSecond)}/s` : job.progress?.phase || job.state}{job.progress?.etaSeconds != null ? ` · ETA ${job.progress.etaSeconds}s` : ""}</small></div>{["queued", "running"].includes(job.state) ? <button className="secondary compact" onClick={() => post(`/api/jobs/${job.id}/cancel`).then(() => toast("Cancelling download")).catch(e => toast(e.message, true))}>Cancel</button> : job.state === "cancelling" ? <span>Cancelling…</span> : <button className="primary compact" onClick={() => download(job.payload.repoId, job.payload.revision)}>Retry / resume</button>}</section>}
    {history.length > 1 && <div className="download-history"><div><History /><span><strong>Download history</strong><small>{history.length} recent jobs</small></span></div><label className="history-picker"><span className="sr-only">Selected download job</span><select aria-label="Selected download job" value={job?.id || ""} onChange={e => api<any>(`/api/jobs/${e.target.value}`).then(setJob).catch(e => toast(e.message, true))}>{history.map(item => <option key={item.id} value={item.id}>{item.payload.repoId} · {item.state} · {item.id.slice(0, 8)}</option>)}</select><ChevronDown /></label></div>}
    {tab === "library" ? <div className="model-list">{models.length === 0 ? <div className="model-empty"><FolderSearch /><h3>No complete checkpoints found</h3><p>Place compatible models in the configured models directory or discover one here.</p><button className="primary" onClick={() => setTab("discover")}>Discover models</button></div> : models.map((model) => <article className="model-row" key={model.id}><div className="model-glyph large">{model.name.slice(0, 2).toUpperCase()}</div><div className="model-name"><div><h3>{model.name}</h3>{model.compatibility === "verified" && <ShieldCheck size={15} />}</div><p>{model.repository || model.architecture}</p><span>{model.parameterCount || "Parameters unknown"} · {model.quantization || "Native weights"} · {formatBytes(model.sizeBytes)}</span></div><div className="model-tags"><Badge tone={model.status === "running" ? "good" : model.status === "failed" ? "bad" : "neutral"}>{model.status}</Badge><Badge tone={model.compatibility === "verified" ? "accent" : model.compatibility === "likely" ? "warn" : "neutral"}>{model.compatibility}</Badge></div><div className="model-actions"><button className="primary compact" title={lifecycle ? "Load this model" : "Lifecycle unavailable while this process is unowned"} disabled={!lifecycle || model.status === "failed" || model.status === "running" || ["starting", "loading", "stopping"].includes(engine.state)} onClick={() => load(model)}><Play size={14} />{model.status === "running" ? "Running" : "Load"}</button><button className="icon-button" disabled={!lifecycle || model.status === "running" || model.status === "failed"} onClick={() => setAdvanced(model)} title="Advanced load"><MoreHorizontal /></button><button className="icon-button danger" disabled={model.status === "running"} onClick={() => setConfirm(model)} title={model.status === "running" ? "Unload before deleting" : "Delete model"}><Trash2 /></button></div>{model.issue && <div className="model-issue"><XCircle size={14} />{model.issue}</div>}</article>)}</div> : <><form className="catalog-search" onSubmit={(event) => { event.preventDefault(); discover(); }}><Search /><input placeholder="Search Hugging Face repositories" value={query} onChange={(event) => setQuery(event.target.value)} /><button className="primary" disabled={searching || query.length < 2}>{searching ? <LoaderCircle className="spin" /> : "Search"}</button></form><label><input type="checkbox" checked={showAll} onChange={e => setShowAll(e.target.checked)} /> Include unverified results on next search</label><p>Verified means listed by upstream, not tested on your hardware. Other results need architecture and quantization review.</p>{catalog.length === 0 && <p>No matching verified models. Try another name or include unverified results.</p>}<div className="catalog-grid">{catalog.map((item) => <article className="catalog-card" key={item.repo}><header><span className="model-glyph">{item.repo.split("/").pop()?.slice(0, 2).toUpperCase()}</span><Badge tone={item.compatibility === "verified" ? "accent" : item.compatibility === "likely" ? "good" : item.compatibility === "unsupported" ? "bad" : "neutral"}>{item.compatibility}</Badge></header><h3>{item.repo.split("/").pop()}</h3><p>{item.family || item.repo.split("/")[0]}{item.architecture ? ` · ${item.architecture}` : ""}</p>{item.notes && <small>{item.notes}</small>}{item.unsupportedReason && <small className="compat-warn">{item.unsupportedReason}</small>}<footer><a href={item.huggingFaceUrl} target="_blank" rel="noreferrer">Details <ExternalLink size={13} /></a><button className="primary compact" onClick={() => download(item.repo)} disabled={!(["verified", "likely"].includes(item.compatibility)) || (job && ["queued", "running"].includes(job.state))}><Download size={14} />Download</button></footer></article>)}</div></>}
    {confirm && <Modal title="Delete checkpoint?" onClose={() => setConfirm(null)}><div className="confirm-body"><div className="warning-mark"><Trash2 /></div><p><strong>{confirm.name}</strong> will be permanently removed from model storage.</p><div className="recovery"><HardDrive /> Reclaims approximately <b>{formatBytes(confirm.sizeBytes)}</b></div><footer><button className="secondary" onClick={() => setConfirm(null)}>Keep model</button><button className="danger-button" onClick={() => remove(confirm)}>Delete permanently</button></footer></div></Modal>}
    {advanced && <Advanced model={advanced} onClose={() => setAdvanced(null)} onLoad={load} />}
  </div>;
}

function Advanced({ model, onClose, onLoad }: { model: Model; onClose: () => void; onLoad: (m: Model, o: any) => void }) {
  const [opts, setOpts] = useState<any>({ gpu: "auto", memoryRatio: 0.9, moeBackend: "auto", maxRunningRequests: 4, maxOutputTokens: 32768 });
  return <Modal title={`Load ${model.name}`} onClose={onClose}><div className="advanced-form"><p>FreeToken resolves hardware and checkpoint defaults automatically. Only override what you understand.</p><div className="field-grid"><label>GPU<input value={opts.gpu} onChange={(event) => setOpts({ ...opts, gpu: event.target.value })} /></label><label>Memory ratio<input type="number" min="0.1" max="1" step="0.05" value={opts.memoryRatio} onChange={(event) => setOpts({ ...opts, memoryRatio: +event.target.value })} /></label><label>MoE strategy<select value={opts.moeBackend} onChange={(event) => setOpts({ ...opts, moeBackend: event.target.value })}><option>auto</option><option>offload</option><option>cpu</option><option>hybrid</option><option>fused</option></select></label><label>Concurrent requests<input type="number" min="1" value={opts.maxRunningRequests} onChange={(event) => setOpts({ ...opts, maxRunningRequests: +event.target.value })} /></label><label>Default output tokens<input type="number" min="1" value={opts.maxOutputTokens} onChange={(event) => setOpts({ ...opts, maxOutputTokens: +event.target.value })} /></label></div><footer><button className="secondary" onClick={onClose}>Cancel</button><button className="primary" onClick={() => onLoad(model, opts)}><Play size={15} />Load model</button></footer></div></Modal>;
}
