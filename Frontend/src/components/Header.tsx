export type ApiStatus = "idle" | "busy" | "ok" | "error";

const STATUS = {
  idle: { label: "Listo", dot: "bg-muted-2", pulse: false },
  busy: { label: "Consultando", dot: "bg-accent-2", pulse: true },
  ok: { label: "En línea", dot: "bg-success", pulse: true },
  error: { label: "Sin conexión", dot: "bg-danger", pulse: false },
} as const;

/**
 * El estado sale de cómo resultó la última consulta, no de un valor fijo. Antes decía
 * "En línea" siempre, incluso con la API caída: el usuario veía el punto verde y un
 * mensaje de error al mismo tiempo.
 */
interface Props {
  status?: ApiStatus;
  /** Reinicia la conversación. Sin esto, el historial crece indefinidamente y el
   *  clasificador de intención puede leer una pregunta nueva como follow-up de un tema
   *  que ya se cerró. */
  onReset?: () => void;
  canReset?: boolean;
}

export default function Header({ status = "idle", onReset, canReset }: Props) {
  const state = STATUS[status];

  return (
    <header className="flex shrink-0 items-center justify-between border-b border-border bg-surface/80 px-5 py-3.5 backdrop-blur">
      <div className="flex items-center gap-3">
        <div className="flex h-9 items-center rounded-lg bg-white px-2 py-1">
          <img src="/logounqcolor.png" alt="Universidad Nacional de Quilmes" className="h-full w-auto object-contain" />
        </div>
        <div className="leading-tight">
          <h1 className="text-sm font-semibold tracking-tight text-text">Asistente Técnico de Mantenimiento</h1>
          <p className="font-mono text-[0.7rem] text-muted-2">Secadero de pastas industrial · Sistema RAG multimodal</p>
        </div>
      </div>

      <div className="flex items-center gap-2">
        {canReset && onReset && (
          <button
            type="button"
            onClick={onReset}
            title="Empezar una conversación nueva (descarta el historial)"
            className="flex items-center gap-1.5 rounded-full border border-border bg-surface-2 px-3 py-1.5 font-mono text-[0.7rem] text-muted transition hover:border-accent-2/50 hover:text-accent-2"
          >
            <svg viewBox="0 0 16 16" fill="none" className="h-3 w-3">
              <path
                d="M13.5 8a5.5 5.5 0 1 1-1.9-4.15M13.5 2v3.5H10"
                stroke="currentColor"
                strokeWidth="1.4"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
            Nueva consulta
          </button>
        )}

        <div className="flex items-center gap-2 rounded-full border border-border bg-surface-2 px-3 py-1.5">
          <span className="relative flex h-2 w-2">
            {state.pulse && (
              <span className={`absolute inline-flex h-full w-full animate-ping rounded-full opacity-60 ${state.dot}`} />
            )}
            <span className={`relative inline-flex h-2 w-2 rounded-full ${state.dot}`} />
          </span>
          <span className="font-mono text-[0.7rem] text-muted">{state.label}</span>
        </div>
      </div>
    </header>
  );
}
