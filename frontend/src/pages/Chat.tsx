import { FormEvent, useEffect, useRef, useState } from "react";
import {
  Bot,
  ChevronDown,
  Copy,
  CornerDownLeft,
  Edit3,
  Menu,
  MessageSquarePlus,
  PanelLeftClose,
  RefreshCw,
  Settings2,
  Square,
  Trash2,
  User,
} from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api, del, formatTime, patch, post } from "../lib/api";
import { Badge, CopyButton } from "../components/ui";
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
    [historyOpen, setHistoryOpen] = useState(true);
  const [settings, setSettings] = useState({
    temperature: 0.7,
    top_p: 0.95,
    max_tokens: 2048,
    systemPrompt: "",
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
  useEffect(() => {
    loadChats().then(async () => {
      const v = await api<{ items: ChatSummary[] }>("/api/chats");
      if (v.items[0]) select(v.items[0].id);
      else create();
    });
  }, []);
  useEffect(() => {
    end.current?.scrollIntoView({ behavior: generating ? "auto" : "smooth" });
  }, [chat?.messages, draft, generating]);
  const send = async (e?: FormEvent, override?: string) => {
    e?.preventDefault();
    const text = (override ?? input).trim();
    if (!text || generating || !chat) return;
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
    setChat({ ...chat, messages: [...(chat.messages || []), user] });
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
              if (doc.error) throw new Error(doc.error);
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
      setGenerating(false);
      setDraft("");
      abort.current = null;
    }
  };
  const remove = async (id: string) => {
    await del(`/api/chats/${id}`);
    setChat(null);
    await loadChats();
    create();
  };
  const editRetry = (index: number) => {
    const msg = chat.messages[index];
    setInput(msg.content);
    setChat({ ...chat, messages: chat.messages.slice(0, index) });
  };
  const ready =
    (engine.state === "ready" || engine.state === "external") &&
    engine.health?.status === "ok";
  return (
    <div className="chat-layout">
      <aside className={historyOpen ? "chat-history" : "chat-history closed"}>
        <header>
          <strong>Conversations</strong>
          <button className="icon-button" onClick={() => setHistoryOpen(false)}>
            <PanelLeftClose />
          </button>
        </header>
        <button className="new-chat" onClick={create}>
          <MessageSquarePlus />
          New chat
        </button>
        <div className="chat-list">
          {chats.map((c) => (
            <button
              className={chat?.id === c.id ? "active" : ""}
              onClick={() => select(c.id)}
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
              onClick={() => setShowSettings(!showSettings)}
            >
              <Settings2 />
            </button>
            {chat && (
              <button
                className="icon-button danger"
                onClick={() => remove(chat.id)}
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
                onBlur={() =>
                  chat &&
                  patch(`/api/chats/${chat.id}`, {
                    systemPrompt: settings.systemPrompt,
                    settings,
                  })
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
                          Reasoning <ChevronDown />
                        </summary>
                        <p>{m.reasoning}</p>
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
                      {m.role === "user" && (
                        <button onClick={() => editRetry(i)}>
                          <Edit3 />
                          Edit & retry
                        </button>
                      )}
                      {m.role === "assistant" && i > 0 && (
                        <button
                          onClick={() =>
                            send(undefined, chat.messages[i - 1].content)
                          }
                        >
                          <RefreshCw />
                          Regenerate
                        </button>
                      )}
                    </div>
                  </div>
                </article>
              ))}
              {draft && (
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
                      <details className="reasoning">
                        <summary>
                          Reasoning <ChevronDown />
                        </summary>
                        <p>{chat.streamReasoning}</p>
                      </details>
                    )}
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>
                      {draft}
                    </ReactMarkdown>
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
          />
          <div>
            <span>
              <kbd>↵</kbd> send · <kbd>⇧↵</kbd> new line
            </span>
            {generating ? (
              <button
                type="button"
                className="stop"
                onClick={() => abort.current?.abort()}
              >
                <Square />
                Stop
              </button>
            ) : (
              <button className="send" disabled={!ready || !input.trim()}>
                <CornerDownLeft />
              </button>
            )}
          </div>
        </form>
      </section>
    </div>
  );
}
