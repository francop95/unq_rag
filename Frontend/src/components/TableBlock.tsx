import { useEffect, useState } from "react";
import { fetchTableMedia } from "../api/client";
import type { TableMedia } from "../types/api";

/**
 * Tabla extraída por Ingestion, renderizada como tabla HTML de verdad.
 *
 * El markdown crudo queda detrás de un toggle en vez de mostrarse siempre arriba de
 * la tabla: era exactamente el mismo contenido dos veces, y en una tabla de
 * troubleshooting de 3 columnas eso son 20 líneas de pipes antes de poder leer nada.
 * Sigue accesible porque es literalmente lo que recibió el LLM.
 */
export default function TableBlock({ mediaPath }: { mediaPath: string }) {
  const [table, setTable] = useState<TableMedia | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showRaw, setShowRaw] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setTable(null);
    setError(null);
    fetchTableMedia(mediaPath)
      .then((data) => {
        if (!cancelled) setTable(data);
      })
      .catch((err: Error) => {
        if (!cancelled) setError(err.message);
      });
    return () => {
      cancelled = true;
    };
  }, [mediaPath]);

  if (error) {
    return <p className="font-mono text-xs text-danger">No se pudo cargar la tabla: {error}</p>;
  }

  if (!table) {
    return (
      <div className="space-y-1.5" aria-label="Cargando tabla">
        <div className="h-7 animate-pulse rounded bg-surface-3" />
        <div className="h-5 w-11/12 animate-pulse rounded bg-surface-3/70" />
        <div className="h-5 w-10/12 animate-pulse rounded bg-surface-3/50" />
      </div>
    );
  }

  const rows = table.json?.rows;
  const hasRows = Boolean(rows && rows.length > 0);

  return (
    <div className="space-y-2">
      {hasRows && !showRaw ? (
        <div className="overflow-x-auto rounded-lg border border-border">
          <table className="w-full border-collapse text-xs">
            <thead className="sticky top-0">
              <tr>
                {rows![0].map((cell, i) => (
                  <th
                    key={i}
                    className="border-b border-border bg-surface-3 px-3 py-2 text-left font-mono text-[0.68rem] font-medium uppercase tracking-wide text-muted"
                  >
                    {cell ?? ""}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows!.slice(1).map((row, ri) => (
                <tr key={ri} className="transition-colors even:bg-surface-2/40 hover:bg-accent-2-soft">
                  {row.map((cell, ci) => (
                    <td
                      key={ci}
                      className="border-b border-border/50 px-3 py-2 align-top text-text/90 last:border-r-0"
                    >
                      {cell ?? ""}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <pre className="max-h-80 overflow-auto rounded-lg border border-border bg-surface-2 p-3 font-mono text-[0.7rem] leading-relaxed whitespace-pre text-text/85">
          {table.markdown || "Tabla sin contenido estructurado."}
        </pre>
      )}

      {hasRows && table.markdown && (
        <button
          type="button"
          onClick={() => setShowRaw((v) => !v)}
          className="font-mono text-[0.65rem] text-muted-2 underline decoration-dotted underline-offset-2 transition hover:text-accent-2"
        >
          {showRaw ? "ver tabla" : "ver markdown crudo (lo que recibió el modelo)"}
        </button>
      )}
    </div>
  );
}
