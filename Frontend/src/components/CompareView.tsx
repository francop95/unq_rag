import { useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { askQuestion, API_BASE_URL, BASELINE_API_BASE_URL, mediaUrl } from "../api/client";
import type { ApiResult, Source } from "../types/api";
import { collectMedia, summarizeMedia } from "../lib/media";
import ChunkText from "./ChunkText";
import Lightbox from "./Lightbox";
import { newId } from "../lib/ids";

/** Una de las dos versiones que se comparan. */
interface Variant {
  key: "multimodal" | "baseline";
  label: string;
  sublabel: string;
  baseUrl: string;
  accent: string;
}

const VARIANTS: Variant[] = [
  {
    key: "multimodal",
    label: "Multimodal",
    sublabel: "visión + OCR + tablas",
    baseUrl: API_BASE_URL,
    accent: "text-accent",
  },
  {
    key: "baseline",
    label: "Solo texto + OCR",
    sublabel: "sin modelo de visión",
    baseUrl: BASELINE_API_BASE_URL,
    accent: "text-accent-2",
  },
];

interface Outcome {
  result?: ApiResult;
  sources: Source[];
  ms: number;
  error?: string;
}

type Outcomes = Partial<Record<Variant["key"], Outcome>>;

const EJEMPLOS = [
  "¿A cuál de los conectores va el cable de red Ethernet en este módulo DI/DO?",
  "¿Qué interruptor térmico está antes del inversor y el motor, el de 10A o el de 25A?",
  "¿En qué bit puedo ver si los parámetros están bloqueados en el PowerFlex 4M?",
];

/** Pregunta a una API midiendo cuánto tarda, sin dejar que un fallo tumbe a la otra. */
async function ask(variant: Variant, query: string, conversationId: string): Promise<Outcome> {
  const t0 = performance.now();
  try {
    // Sin historial a propósito: la comparación tiene que medir la misma pregunta
    // contra los dos índices, y un follow-up reescrito introduce una variable más.
    const results = await askQuestion(query, conversationId, [], variant.baseUrl);
    const result = results[0];
    return {
      result,
      sources: (result?.sources || []).filter((s) => s.file_name),
      ms: Math.round(performance.now() - t0),
    };
  } catch (err) {
    return {
      sources: [],
      ms: Math.round(performance.now() - t0),
      error: err instanceof Error ? err.message : "Error desconocido",
    };
  }
}

function Metric({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="flex flex-col" title={hint}>
      <span className="font-mono text-[0.6rem] uppercase tracking-wider text-muted-2">{label}</span>
      <span className="font-mono text-[0.8rem] tabular-nums text-text">{value}</span>
    </div>
  );
}

function SourceRow({ source, number }: { source: Source; number: number }) {
  const [open, setOpen] = useState(false);
  const hasText = Boolean(source.text && source.text.trim());
  const score = source.similarity_score ?? 0;
  const color = score >= 70 ? "text-success" : score >= 50 ? "text-accent" : "text-danger";

  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface-2/50">
      <button
        type="button"
        onClick={() => hasText && setOpen((v) => !v)}
        disabled={!hasText}
        className="flex w-full items-center gap-2 px-2.5 py-2 text-left transition hover:bg-surface-3/40 disabled:cursor-default"
      >
        <span className="flex h-4 w-4 shrink-0 items-center justify-center rounded bg-surface-3 font-mono text-[0.6rem] text-muted-2">
          {number}
        </span>
        <span className="min-w-0 flex-1 truncate font-mono text-[0.68rem] text-muted">
          {source.file_name}
          <span className="text-muted-2"> · p.{source.page}</span>
        </span>
        {source.is_attached_plan ? (
          <span className="shrink-0 font-mono text-[0.6rem] text-muted-2">adjunto</span>
        ) : (
          <span className={`shrink-0 font-mono text-[0.68rem] tabular-nums ${color}`}>
            {score.toFixed(0)}%
          </span>
        )}
        {hasText && (
          <svg
            viewBox="0 0 16 16"
            fill="none"
            className={`h-3 w-3 shrink-0 text-muted-2 transition-transform ${open ? "rotate-180" : ""}`}
          >
            <path d="M4 6l4 4 4-4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
          </svg>
        )}
      </button>
      {open && hasText && (
        <div className="border-t border-border/60 p-2.5">
          <ChunkText text={source.text!} />
        </div>
      )}
    </div>
  );
}

function VariantColumn({ variant, outcome, loading }: {
  variant: Variant;
  outcome?: Outcome;
  loading: boolean;
}) {
  const [lightboxIndex, setLightboxIndex] = useState<number | null>(null);

  const mediaEntries = useMemo(
    () => collectMedia(outcome?.sources || []),
    [outcome?.sources],
  );

  // Miniaturas de las imágenes, CON su índice dentro de mediaEntries.
  //
  // El índice hay que arrastrarlo: el Lightbox recibe la lista completa
  // (incluidas las tablas, que también sabe renderizar) y navega por posición.
  // Si se le pasara el índice del array filtrado, abriría otra pieza.
  const thumbs = useMemo(
    () =>
      mediaEntries
        .map((entry, index) => ({ entry, index }))
        .filter(({ entry }) => entry.kind === "image"),
    [mediaEntries],
  );

  const docs = useMemo(() => {
    const set = new Set((outcome?.sources || []).filter((s) => !s.is_attached_plan).map((s) => s.file_name));
    return Array.from(set);
  }, [outcome?.sources]);

  const topScore = outcome?.sources?.find((s) => !s.is_attached_plan)?.similarity_score;

  return (
    <section className="flex min-w-0 flex-col rounded-xl border border-border bg-surface">
      <header className="flex items-center justify-between gap-2 border-b border-border px-3.5 py-2.5">
        <div className="leading-tight">
          <h2 className={`text-sm font-semibold ${variant.accent}`}>{variant.label}</h2>
          <p className="font-mono text-[0.62rem] text-muted-2">{variant.sublabel}</p>
        </div>
        <code className="shrink-0 rounded bg-surface-2 px-1.5 py-0.5 font-mono text-[0.58rem] text-muted-2">
          {variant.baseUrl.replace(/^https?:\/\//, "")}
        </code>
      </header>

      {loading && (
        <div className="flex items-center gap-2 px-3.5 py-6 font-mono text-[0.7rem] text-muted-2">
          <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-accent-2" />
          consultando…
        </div>
      )}

      {!loading && outcome?.error && (
        <div className="m-3.5 rounded-lg border border-danger/30 bg-danger/5 p-3 text-[0.78rem] text-danger">
          <p className="font-medium">No respondió.</p>
          <p className="mt-1 font-mono text-[0.68rem] opacity-80">{outcome.error}</p>
          <p className="mt-2 text-[0.72rem] opacity-90">
            ¿Está levantada esta API? <code className="font-mono">{variant.baseUrl}</code>
          </p>
        </div>
      )}

      {!loading && outcome && !outcome.error && (
        <>
          <div className="grid grid-cols-4 gap-2 border-b border-border/60 px-3.5 py-2.5">
            <Metric label="tiempo" value={`${(outcome.ms / 1000).toFixed(1)}s`} />
            <Metric label="fuentes" value={String(outcome.sources.length)} />
            <Metric
              label="media"
              value={String(mediaEntries.length)}
              hint={summarizeMedia(mediaEntries) || "sin imágenes ni tablas"}
            />
            <Metric label="top sim" value={topScore != null ? `${topScore.toFixed(0)}%` : "—"} />
          </div>

          {docs.length > 0 && (
            <div className="flex flex-wrap gap-1 border-b border-border/60 px-3.5 py-2">
              {docs.map((d) => (
                <span
                  key={d}
                  className="truncate rounded bg-surface-2 px-1.5 py-0.5 font-mono text-[0.6rem] text-muted"
                  title={d}
                >
                  {d.replace(/\.pdf$/i, "")}
                </span>
              ))}
            </div>
          )}

          <div className="answer-markdown min-h-[4rem] px-3.5 py-3 text-sm leading-relaxed text-text">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>
              {outcome.result?.answer?.trim() || "_(sin respuesta)_"}
            </ReactMarkdown>
          </div>

          {thumbs.length > 0 && (
            <div className="flex gap-1.5 overflow-x-auto border-t border-border/60 px-3.5 py-2.5">
              {thumbs.map(({ entry, index }) => (
                <button
                  key={entry.path}
                  type="button"
                  onClick={() => setLightboxIndex(index)}
                  title={`${entry.fileName} · p.${entry.page} — clic para ampliar`}
                  className="group relative h-16 w-16 shrink-0 overflow-hidden rounded border border-border transition hover:border-accent-2"
                >
                  <img
                    src={mediaUrl(entry.path)}
                    alt={`${entry.fileName} p.${entry.page}`}
                    className="h-full w-full object-cover transition group-hover:opacity-80"
                  />
                  <span className="absolute inset-0 flex items-center justify-center opacity-0 transition group-hover:opacity-100">
                    <svg viewBox="0 0 16 16" fill="none" className="h-4 w-4 text-white drop-shadow">
                      <circle cx="7" cy="7" r="4.5" stroke="currentColor" strokeWidth="1.6" />
                      <path d="M10.5 10.5L14 14M7 5.2v3.6M5.2 7h3.6" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
                    </svg>
                  </span>
                </button>
              ))}
            </div>
          )}

          {lightboxIndex !== null && (
            <Lightbox
              entries={mediaEntries}
              index={lightboxIndex}
              onClose={() => setLightboxIndex(null)}
              onNavigate={setLightboxIndex}
            />
          )}

          <div className="border-t border-border/60 px-3.5 py-2.5">
            <p className="mb-1.5 font-mono text-[0.6rem] uppercase tracking-wider text-muted-2">
              contextos recuperados ({outcome.sources.length})
            </p>
            {outcome.sources.length === 0 ? (
              <p className="py-2 font-mono text-[0.7rem] text-muted-2">
                nada superó el gate de relevancia
              </p>
            ) : (
              <div className="flex flex-col gap-1.5">
                {outcome.sources.map((s, i) => (
                  <SourceRow key={`${s.file_name}-${s.page}-${i}`} source={s} number={i + 1} />
                ))}
              </div>
            )}
          </div>
        </>
      )}
    </section>
  );
}

export default function CompareView() {
  const [query, setQuery] = useState("");
  const [asked, setAsked] = useState("");
  const [outcomes, setOutcomes] = useState<Outcomes>({});
  const [loading, setLoading] = useState(false);
  const conversationId = useRef(newId());

  async function run(q: string) {
    const trimmed = q.trim();
    if (!trimmed || loading) return;

    setAsked(trimmed);
    setOutcomes({});
    setLoading(true);

    // En paralelo: son dos servicios distintos y esperar uno detrás del otro solo
    // haría más lenta la comparación.
    const results = await Promise.all(
      VARIANTS.map((v) => ask(v, trimmed, conversationId.current)),
    );

    const next: Outcomes = {};
    VARIANTS.forEach((v, i) => {
      next[v.key] = results[i];
    });
    setOutcomes(next);
    setLoading(false);
  }

  const ambos = VARIANTS.every((v) => outcomes[v.key] && !outcomes[v.key]!.error);
  const docsPorVariante = VARIANTS.map((v) =>
    new Set((outcomes[v.key]?.sources || [])
      .filter((s) => !s.is_attached_plan)
      .map((s) => s.file_name)),
  );
  const mismosDocs =
    ambos &&
    docsPorVariante[0].size > 0 &&
    docsPorVariante[0].size === docsPorVariante[1].size &&
    [...docsPorVariante[0]].every((d) => docsPorVariante[1].has(d));

  return (
    <div className="mx-auto flex max-w-[100rem] flex-col gap-4 px-4 py-5">
      <div className="rounded-xl border border-border bg-surface p-3.5">
        <form
          onSubmit={(e) => {
            e.preventDefault();
            run(query);
          }}
          className="flex gap-2"
        >
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Escribí una pregunta y se la hago a las dos versiones…"
            disabled={loading}
            className="min-w-0 flex-1 rounded-lg border border-border bg-surface-2 px-3 py-2 text-sm text-text outline-none transition placeholder:text-muted-2 focus:border-accent-2/60 disabled:opacity-60"
          />
          <button
            type="submit"
            disabled={loading || !query.trim()}
            className="shrink-0 rounded-lg bg-accent px-4 py-2 text-sm font-medium text-bg transition hover:opacity-90 disabled:opacity-40"
          >
            {loading ? "Comparando…" : "Comparar"}
          </button>
        </form>

        {!asked && (
          <div className="mt-2.5 flex flex-wrap gap-1.5">
            {EJEMPLOS.map((ej) => (
              <button
                key={ej}
                type="button"
                onClick={() => {
                  setQuery(ej);
                  run(ej);
                }}
                className="rounded-full border border-border bg-surface-2 px-2.5 py-1 text-left font-mono text-[0.65rem] text-muted transition hover:border-accent-2/50 hover:text-accent-2"
              >
                {ej.length > 72 ? `${ej.slice(0, 72)}…` : ej}
              </button>
            ))}
          </div>
        )}
      </div>

      {asked && (
        <>
          <div className="flex flex-wrap items-center justify-between gap-2 px-1">
            <p className="text-sm text-muted">
              <span className="font-mono text-[0.65rem] uppercase tracking-wider text-muted-2">pregunta · </span>
              {asked}
            </p>
            {ambos && (
              <span
                className={`shrink-0 rounded-full border px-2.5 py-1 font-mono text-[0.65rem] ${
                  mismosDocs
                    ? "border-border bg-surface-2 text-muted"
                    : "border-accent-2/40 bg-accent-2-soft/30 text-accent-2"
                }`}
              >
                {mismosDocs
                  ? "mismos documentos recuperados"
                  : "recuperaron documentos distintos"}
              </span>
            )}
          </div>

          <div className="grid grid-cols-1 items-start gap-4 lg:grid-cols-2">
            {VARIANTS.map((v) => (
              <VariantColumn
                key={v.key}
                variant={v}
                outcome={outcomes[v.key]}
                loading={loading}
              />
            ))}
          </div>
        </>
      )}
    </div>
  );
}
