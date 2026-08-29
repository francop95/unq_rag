import { useEffect, useState } from "react";

/**
 * Etapas reales del pipeline, con el tiempo aproximado en que ocurren. Medido sobre
 * la API: ~1s de embedding + búsqueda híbrida, después la llamada al LLM con los dos
 * planos PDF adjuntos, que es lo que se lleva los 10-15s restantes.
 *
 * Es una estimación por tiempo, no progreso real (la API responde de una sola vez),
 * pero convierte una espera de 15 segundos en algo que se entiende. Con tres puntitos
 * y sin feedback, esa espera se lee como que la app se colgó.
 */
const STAGES = [
  { at: 0, label: "Buscando en los manuales…" },
  { at: 2000, label: "Cruzando texto, tablas y diagramas…" },
  { at: 5000, label: "Leyendo los planos eléctricos…" },
  { at: 9000, label: "Redactando el diagnóstico…" },
  { at: 20000, label: "Casi listo, la consulta es densa…" },
];

export default function TypingIndicator() {
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    const start = Date.now();
    const timer = setInterval(() => setElapsed(Date.now() - start), 250);
    return () => clearInterval(timer);
  }, []);

  const stageIndex = STAGES.reduce((acc, stage, i) => (elapsed >= stage.at ? i : acc), 0);
  const seconds = Math.floor(elapsed / 1000);

  return (
    <div className="flex animate-fade-in-up items-start gap-3">
      <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-accent/30 bg-accent-soft text-accent">
        <svg viewBox="0 0 24 24" fill="none" className="h-4 w-4 animate-spin">
          <path d="M12 3a9 9 0 1 0 9 9" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
        </svg>
      </div>

      <div className="min-w-0 flex-1 rounded-2xl rounded-tl-sm border border-border bg-surface px-4 py-3">
        <div className="flex items-center gap-2">
          <span key={stageIndex} className="animate-fade-in-up text-sm text-muted">
            {STAGES[stageIndex].label}
          </span>
          <span className="font-mono text-[0.65rem] tabular-nums text-muted-2">{seconds}s</span>
        </div>

        {/* Etapas ya pasadas, como rastro de lo que viene haciendo */}
        <div className="mt-2.5 flex gap-1">
          {STAGES.slice(0, 4).map((stage, i) => (
            <span
              key={stage.label}
              className={`h-0.5 flex-1 rounded-full transition-colors duration-500 ${
                i < stageIndex ? "bg-accent-2/70" : i === stageIndex ? "bg-accent-2/40" : "bg-surface-3"
              }`}
            />
          ))}
        </div>
      </div>
    </div>
  );
}
