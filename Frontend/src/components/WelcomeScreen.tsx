const CAPABILITIES = [
  {
    label: "Texto y tablas",
    detail: "Manuales, parámetros y tablas de fallas",
    icon: (
      <>
        <path d="M2.5 3h11v10h-11z" stroke="currentColor" strokeWidth="1.2" />
        <path d="M2.5 6.2h11M6 6.2V13" stroke="currentColor" strokeWidth="1.2" />
      </>
    ),
  },
  {
    label: "Planos y diagramas",
    detail: "Interpreta los planos eléctricos adjuntos",
    icon: (
      <>
        <rect x="2.25" y="3.25" width="11.5" height="9.5" rx="1" stroke="currentColor" strokeWidth="1.2" />
        <path d="M5 6h3M5 8.5h6M5 11h4" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" />
      </>
    ),
  },
  {
    label: "Cita las fuentes",
    detail: "Documento, página y fragmento exacto",
    icon: (
      <>
        <path d="M4 2.5h5.5L12.5 5.5v8H4z" stroke="currentColor" strokeWidth="1.2" strokeLinejoin="round" />
        <path d="M9.25 2.5v3.25h3.25" stroke="currentColor" strokeWidth="1.2" strokeLinejoin="round" />
      </>
    ),
  },
];

const EXAMPLES = [
  {
    category: "Falla",
    query: "El variador no arranca desde el teclado integrado, ¿qué reviso?",
    hint: "tabla de acciones correctivas",
  },
  {
    category: "Plano",
    query: "Mostrame el diagrama de conexionado de los bornes de control del variador",
    hint: "diagramas del manual",
  },
  {
    category: "Proceso",
    query: "¿Cómo se mide la humedad del aire en el secadero?",
    hint: "tesis del secadero",
  },
  {
    category: "Diagnóstico",
    query: "El motor no responde a los cambios de velocidad, ¿qué reviso primero?",
    hint: "parámetros y cableado",
  },
];

export default function WelcomeScreen({ onExample }: { onExample: (q: string) => void }) {
  return (
    <div className="mx-auto flex min-h-full max-w-2xl flex-col items-center justify-center px-6 py-10 text-center">
      <div className="glow-accent mb-5 flex h-16 w-16 items-center justify-center rounded-2xl border border-accent/30 bg-accent-soft text-accent">
        <svg viewBox="0 0 24 24" fill="none" className="h-8 w-8">
          <path d="M3 21h18M5 21V8l7-4 7 4v13M9 21v-6h6v6" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" />
        </svg>
      </div>

      <h2 className="text-xl font-semibold tracking-tight text-text">¿Qué falla estás viendo?</h2>
      <p className="mt-2 max-w-md text-sm leading-relaxed text-muted">
        Preguntame sobre fallas, cableado, parámetros o el funcionamiento del secadero. Te muestro los
        diagramas y tablas exactas de donde sale cada respuesta.
      </p>

      <div className="mt-7 grid w-full grid-cols-1 gap-2 sm:grid-cols-3">
        {CAPABILITIES.map((cap) => (
          <div
            key={cap.label}
            className="rounded-xl border border-border bg-surface/60 px-3 py-3 text-left"
          >
            <span className="mb-1.5 flex h-6 w-6 items-center justify-center rounded-md border border-accent-2/25 bg-accent-2-soft text-accent-2">
              <svg viewBox="0 0 16 16" fill="none" className="h-3.5 w-3.5">
                {cap.icon}
              </svg>
            </span>
            <p className="text-xs font-medium text-text">{cap.label}</p>
            <p className="mt-0.5 font-mono text-[0.65rem] leading-snug text-muted-2">{cap.detail}</p>
          </div>
        ))}
      </div>

      <p className="mt-8 mb-2.5 self-start font-mono text-[0.68rem] uppercase tracking-wide text-muted-2">
        Probá con
      </p>
      <div className="grid w-full grid-cols-1 gap-2 sm:grid-cols-2">
        {EXAMPLES.map((example) => (
          <button
            key={example.query}
            type="button"
            onClick={() => onExample(example.query)}
            className="group flex flex-col gap-1.5 rounded-xl border border-border bg-surface px-3.5 py-3 text-left transition duration-200 hover:-translate-y-0.5 hover:border-accent-2/50 hover:bg-surface-2 hover:shadow-lg hover:shadow-accent-2/5"
          >
            <span className="flex items-center gap-2">
              <span className="rounded border border-accent-2/25 bg-accent-2-soft px-1.5 py-px font-mono text-[0.58rem] uppercase tracking-wide text-accent-2">
                {example.category}
              </span>
              <span className="font-mono text-[0.6rem] text-muted-2">{example.hint}</span>
            </span>
            <span className="text-xs leading-snug text-muted transition group-hover:text-text">
              {example.query}
            </span>
          </button>
        ))}
      </div>
    </div>
  );
}
