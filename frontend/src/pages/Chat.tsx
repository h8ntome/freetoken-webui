import { FormEvent, useEffect, useRef, useState } from "react";
import {
  Bot,
  BrainCircuit,
  ChevronDown,
  CornerDownLeft,
  Menu,
  MessageSquarePlus,
  PanelLeftClose,
  Settings2,
  Square,
  Trash2,
  User,
} from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api, del, formatTime, patch, post } from "../lib/api";
import { Badge, CopyButton, Modal } from "../components/ui";
import type { ChatSummary, EngineStatus, Message } from "../types";

export default function Chat({
  engine,
  toast,
}: {
  engine: EngineStatus;
  toast: (s: string, b?: boolean) => void;
}) {
  const [chats, setChats] = useState<ChatSummary[]>([]),
    [chat, setChat] = useState<any>(null),
    [input, setInput] = useState(""),
    [generating, setGenerating] = useState(false),
    [draft, setDraft] = useState(""),
    [showSettings, setShowSettings] = useState(false),
    [historyOpen, setHistoryOpen] = useState(true),
    [confirmDelete, setConfirmDelete] = useState(false);
  const [settings, setSettings] = useState({
    temperature: 0.7,
    top_p: 0.95,
    max_tokens: 2048,
    systemPrompt: "",
    enableThinking: true,
  });
  const abort = useRef<AbortController | null>(null),
    end = useRef<HTMLDivElement | null>(null);
  const loadChats = () =>
    api<{ items: ChatSummary[] }>("/api/chats").then((v) => setChats(v.items));
  const select = async (id: string) => {
    const v = await api<any>(`/api/chats/${id}`);
    setChat(v);
    setSettings((s) => ({
      ...s,
      ...v.settings,
      systemPrompt: v.systemPrompt || "",
    }));
  };
  const create = async () => {
    const v = await post<ChatSummary>("/api/chats", {
      model: engine.model,
      title: "New chat",
      settings,
    });
    await loadChats();
    await select(v.id);
  };
  const saveSettings = async () => {
    if (!chat) return;
    await patch(`/api/chats/${chat.id}`, {
      systemPrompt: settings.systemPrompt,
      settings,
    });
    toast("Chat settings saved");
  };
  useEffect(() => {
    loadChats().then(async () => {
      const v = await api<{ items: ChatSummary[] }>("/api/chats");
      if (v.items[0]) await select(v.items[0].id);
      else await create();
    }).catch(e => toast(e.message, true));
    return () => abort.current?.abort();
  }, []);
  useEffect(() => {
    end.current?.scrollIntoView({ behavior: generating ? "auto" : "smooth" });
  }, [chat?.messages, draft, generating]);
  const send = async (e?: FormEvent, override?: string) => {
    e?.preventDefault();
    const text = (override ?? input).trim();
    if (!text || generating || !chat || !ready) return;
    const user: Message = { role: "user", content: text };
    const messages = [
      ...(chat.messages || []).map((m: Message) => ({
        role: m.role,
        content: m.content,
      })),
      user,
    ];
    if (settings.systemPrompt)
      messages.unshift({ role: "system", content: settings.systemPrompt });
    setChat({ ...chat, messages: [...(chat.messages || []), user], streamReasoning: "" });
    setInput("");
    setDraft("");
    setGenerating(true);
    abort.current = new AbortController();
    try {
      const response = await fetch("/api/chat/completions", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": (await api<any>("/api/auth/me")).csrfToken,
        },
        body: JSON.stringify({
          chatId: chat.id,
          messages,
          model: engine.model,
          temperature: settings.temperature,
          top_p: settings.top_p,
          max_tokens: settings.max_tokens,
          chat_template_kwargs: { enable_thinking: settings.enableThinking },
        }),
        signal: abort.current.signal,
      });
      if (!response.ok)
        throw new Error((await response.json()).detail || "Generation failed");
      const reader = response.body!.getReader(),
        decoder = new TextDecoder();
      let buffer = "",
        content = "",
        reasoning = "";
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        buffer = buffer.replace(/\r\n/g, "\n");
        while (buffer.includes("\n\n")) {
          const boundary = buffer.indexOf("\n\n");
          const event = buffer.slice(0, boundary);
          buffer = buffer.slice(boundary + 2);
          for (const line of event.split("\n"))
            if (line.startsWith("data:")) {
              const raw = line.slice(5).trim();
              if (raw === "[DONE]") continue;
              let doc: any;
              try {
                doc = JSON.parse(raw);
              } catch {
                continue;
              }
              if (doc.error) throw new Error(typeof doc.error === "string" ? doc.error : doc.error.message || JSON.stringify(doc.error));
              const d = doc.choices?.[0]?.delta || {};
              content += d.content || "";
              reasoning += d.reasoning_content || d.reasoning || "";
              setDraft(content);
              setChat((c: any) => ({ ...c, streamReasoning: reasoning }));
            }
        }
      }
      await select(chat.id);
      await loadChats();
    } catch (e: any) {
      if (e.name !== "AbortError") toast(e.message, true);
    } finally {
      await select(chat.id).catch(e => toast(e.message, true));
      setGenerating(false);
      setDraft("");
      abort.current = null;
    }
  };
  const remove = async (id: string) => {
    await del(`/api/chats/${id}`);
    setChat(null);
    await loadChats();
    await create();
  };
  const ready =
    (engine.state === "ready" || engine.state === "external") &&
    engine.health?.status === "ok";
  return (
    <div className="chat-layout">
      <aside className={historyOpen ? "chat-history" : "chat-history closed"}>
        <header>
          <strong>Conversations</strong>
          <button className="icon-button" aria-label="Close conversation history" title="Close conversation history" onClick={() => setHistoryOpen(false)}>
            <PanelLeftClose />
          </button>
        </header>
        <button className="new-chat" disabled={generating} onClick={() => create().catch(e => toast(e.message, true))}>
          <MessageSquarePlus />
          New chat
        </button>
        <div className="chat-list">
          {chats.map((c) => (
            <button
              className={chat?.id === c.id ? "active" : ""}
              disabled={generating} onClick={() => select(c.id).catch(e => toast(e.message, true))}
              key={c.id}
            >
              <span>{c.title}</span>
              <small>{formatTime(c.updatedAt)}</small>
            </button>
          ))}
        </div>
      </aside>
      <section className="chat-main">
        <header className="chat-top">
          {!historyOpen && (
            <button
              className="icon-button"
              aria-label="Open conversation history"
              title="Open conversation history"
              onClick={() => setHistoryOpen(true)}
            >
              <Menu />
            </button>
          )}
          <div>
            <strong>{chat?.title || "New chat"}</strong>
            <span>
              <i className={ready ? "green" : "amber"} />
              {engine.model || "No model loaded"}
            </span>
          </div>
          <div>
            <Badge tone={ready ? "good" : "warn"}>{engine.state}</Badge>
            <button
              className="icon-button"
              aria-label="Chat settings"
              title="Chat settings"
              onClick={() => setShowSettings(!showSettings)}
            >
              <Settings2 />
            </button>
            {chat && (
              <button
                className="icon-button danger"
                aria-label="Delete conversation"
                title="Delete conversation"
                disabled={generating} onClick={() => setConfirmDelete(true)}
              >
                <Trash2 />
              </button>
            )}
          </div>
        </header>
        {showSettings && (
          <div className="chat-settings">
            <label>
              System prompt
              <textarea
                value={settings.systemPrompt}
                onChange={(e) =>
                  setSettings({ ...settings, systemPrompt: e.target.value })
                }
              />
            </label>
            <label>
              Temperature
              <input
                type="number"
                step=".1"
                min="0"
                max="2"
                value={settings.temperature}
                onChange={(e) =>
                  setSettings({ ...settings, temperature: +e.target.value })
                }
              />
            </label>
            <label>
              Top P
              <input
                type="number"
                step=".05"
                min="0"
                max="1"
                value={settings.top_p}
                onChange={(e) =>
                  setSettings({ ...settings, top_p: +e.target.value })
                }
              />
            </label>
            <label>
              Max output
              <input
                type="number"
                min="1"
                value={settings.max_tokens}
                onChange={(e) =>
                  setSettings({ ...settings, max_tokens: +e.target.value })
                }
              />
            </label>
            <label className="thinking-setting">
              Model thinking
              <span>
                <input
                  type="checkbox"
                  checked={settings.enableThinking}
                  onChange={(e) => setSettings({ ...settings, enableThinking: e.target.checked })}
                />
                Show reasoning when supported
              </span>
            </label>
            <div className="chat-settings-actions">
              <small>Applied to new messages</small>
              <button className="secondary compact" onClick={() => saveSettings().catch(e => toast(e.message, true))}>Save settings</button>
            </div>
          </div>
        )}
        <div className="messages">
          {!chat?.messages?.length && !draft ? (
            <div className="chat-empty">
              <span className="status-rings">
                <i />
                <i />
                <Bot />
              </span>
              <div className="eyebrow">READY WHEN YOU ARE</div>
              <h2>What should we explore?</h2>
              <p>Your conversation is stored locally on this server.</p>
              <div>
                {[
                  "Explain this codebase",
                  "Write a GPU monitoring script",
                  "Compare MoE cache strategies",
                ].map((x) => (
                  <button onClick={() => setInput(x)} key={x}>
                    {x}
                  </button>
                ))}
              </div>
            </div>
          ) : (
            <>
              {chat?.messages?.map((m: Message, i: number) => (
                <article className={`message ${m.role}`} key={m.id || i}>
                  <div className="message-avatar">
                    {m.role === "user" ? <User /> : <Bot />}
                  </div>
                  <div className="message-body">
                    <header>
                      <strong>
                        {m.role === "user"
                          ? "You"
                          : engine.model || "FreeToken"}
                      </strong>
                      <span>
                        {m.createdAt ? formatTime(m.createdAt) : "now"}
                      </span>
                    </header>
                    {m.reasoning && (
                      <details className="reasoning">
                        <summary>
                          <BrainCircuit />
                          <span>Thinking</span>
                          <small>View reasoning</small>
                          <ChevronDown />
                        </summary>
                        <div className="reasoning-content">{m.reasoning}</div>
                      </details>
                    )}
                    <ReactMarkdown
                      remarkPlugins={[remarkGfm]}
                      components={{
                        code({ children, className }) {
                          const text = String(children).replace(/\n$/, "");
                          return (
                            <span className="code-wrap">
                              <code className={className}>{children}</code>
                              <CopyButton value={text} label="" />
                            </span>
                          );
                        },
                      }}
                    >
                      {m.content}
                    </ReactMarkdown>
                    <div className="message-actions">
                      <CopyButton value={m.content} />
                      {m.usage?.total_tokens != null && <span>{m.usage.total_tokens} tokens</span>}
                    </div>
                  </div>
                </article>
              ))}
              {(draft || chat?.streamReasoning || generating) && (
                <article className="message assistant">
                  <div className="message-avatar">
                    <Bot />
                  </div>
                  <div className="message-body">
                    <header>
                      <strong>{engine.model}</strong>
                      <span className="typing">GENERATING</span>
                    </header>
                    {chat.streamReasoning && (
                      <details className="reasoning live" open>
                        <summary>
                          <BrainCircuit />
                          <span>Thinking</span>
                          <small>Live</small>
                          <ChevronDown />
                        </summary>
                        <div className="reasoning-content">{chat.streamReasoning}</div>
                      </details>
                    )}
                    {draft ? <ReactMarkdown remarkPlugins={[remarkGfm]}>{draft}</ReactMarkdown> : !chat.streamReasoning && <p className="thinking-placeholder">Formulating response…</p>}
                    <span className="cursor" />
                  </div>
                </article>
              )}
            </>
          )}
          <div ref={end} />
        </div>
        <form className="composer" onSubmit={send}>
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                send();
              }
            }}
            placeholder={
              ready
                ? "Message your local model…"
                : "Load a model to start chatting"
            }
            disabled={!ready}
            rows={1}
            aria-label="Message your local model"
          />
          <div>
            <span>
              <kbd>↵</kbd> send · <kbd>⇧↵</kbd> new line
            </span>
            {generating ? (
              <button
                type="button"
                className="stop"
                aria-label="Stop generating"
                title="Stop generating"
                onClick={() => abort.current?.abort()}
              >
                <Square />
                Stop
              </button>
            ) : (
              <button className="send" aria-label="Send message" title="Send message" disabled={!ready || !input.trim()}>
                <CornerDownLeft />
              </button>
            )}
          </div>
        </form>
      </section>
      {confirmDelete && chat && <Modal title="Delete conversation?" onClose={() => setConfirmDelete(false)}><div className="confirm-body"><div className="warning-mark"><Trash2 /></div><p><strong>{chat.title}</strong> and its messages will be permanently removed.</p><footer><button className="secondary" onClick={() => setConfirmDelete(false)}>Keep conversation</button><button className="danger-button" onClick={() => remove(chat.id).then(() => setConfirmDelete(false)).catch(e => toast(e.message, true))}>Delete conversation</button></footer></div></Modal>}
    </div>
  );
}
