import { useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { ChatMessage as ChatMessageT } from "../types/chat";
import { collectMedia } from "../lib/media";
import SourcesPanel from "./SourcesPanel";
import MediaGallery from "./MediaGallery";
import Lightbox from "./Lightbox";

function AssistantAvatar({ error }: { error?: boolean }) {
  return (
    <div
      className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border ${
        error ? "border-danger/40 bg-danger/10 text-danger" : "border-accent/30 bg-accent-soft text-accent"
      }`}
    >
      <svg viewBox="0 0 24 24" fill="none" className="h-4 w-4">
        <path
          d="M12 2v3M12 19v3M4.2 4.2l2.1 2.1M17.7 17.7l2.1 2.1M2 12h3M19 12h3M4.2 19.8l2.1-2.1M17.7 6.3l2.1-2.1"
          stroke="currentColor"
          strokeWidth="1.6"
          strokeLinecap="round"
        />
        <circle cx="12" cy="12" r="4.2" stroke="currentColor" strokeWidth="1.6" />
      </svg>
    </div>
  );
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
    } catch {
      // Sin permiso de clipboard (o contexto no seguro): no hay nada que informar.
    }
  }

  return (
    <button
      type="button"
      onClick={copy}
      title="Copiar respuesta"
      className="flex items-center gap-1 rounded-md px-1.5 py-1 font-mono text-[0.62rem] text-muted-2 transition hover:bg-surface-3 hover:text-accent-2"
    >
      {copied ? (
        <>
          <svg viewBox="0 0 16 16" fill="none" className="h-3 w-3">
            <path d="M3 8.5l3.5 3.5L13 5" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
          copiado
        </>
      ) : (
        <>
          <svg viewBox="0 0 16 16" fill="none" className="h-3 w-3">
            <rect x="5.5" y="5.5" width="8" height="8" rx="1.3" stroke="currentColor" strokeWidth="1.3" />
            <path d="M10.5 3.5a1.5 1.5 0 0 0-1.5-1.5H4A1.5 1.5 0 0 0 2.5 3.5v5A1.5 1.5 0 0 0 4 10" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
          </svg>
          copiar
        </>
      )}
    </button>
  );
}

export default function ChatMessage({ message }: { message: ChatMessageT }) {
  const [lightboxIndex, setLightboxIndex] = useState<number | null>(null);

  const mediaEntries = useMemo(() => collectMedia(message.sources || []), [message.sources]);

  if (message.role === "user") {
    return (
      <div className="flex animate-fade-in-up justify-end">
        <div className="max-w-[80%] rounded-2xl rounded-tr-sm bg-accent px-4 py-2.5 text-sm font-medium text-bg shadow-lg shadow-accent/10">
          {message.content}
        </div>
      </div>
    );
  }

  return (
    <div className="flex animate-fade-in-up items-start gap-3">
      <AssistantAvatar error={message.isError} />
      <div className="min-w-0 flex-1">
        <div
          className={`rounded-2xl rounded-tl-sm border text-sm leading-relaxed ${
            message.isError
              ? "border-danger/30 bg-danger/5 text-danger"
              : "border-border bg-surface text-text"
          }`}
        >
          <div className="answer-markdown px-4 py-3">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{message.content}</ReactMarkdown>
          </div>

          {!message.isError && (
            <div className="flex items-center justify-end border-t border-border/60 px-2 py-1">
              <CopyButton text={message.content} />
            </div>
          )}
        </div>

        <MediaGallery entries={mediaEntries} onOpen={setLightboxIndex} />

        {message.sources && (
          <SourcesPanel
            sources={message.sources}
            mediaEntries={mediaEntries}
            onOpenMedia={setLightboxIndex}
          />
        )}
      </div>

      {lightboxIndex !== null && (
        <Lightbox
          entries={mediaEntries}
          index={lightboxIndex}
          onClose={() => setLightboxIndex(null)}
          onNavigate={setLightboxIndex}
        />
      )}
    </div>
  );
}
