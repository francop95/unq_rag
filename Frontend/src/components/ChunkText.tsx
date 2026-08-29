const MARKERS = {
  prev: "[CONTEXTO PREVIO]",
  relevant: "[CHUNK RELEVANTE]",
  next: "[CONTEXTO SIGUIENTE]",
} as const;

interface Segment {
  role: "prev" | "relevant" | "next";
  text: string;
}

/**
 * Parte el texto en los marcadores que inyecta el context expander de la API. Sin
 * esto los marcadores se mostraban crudos al usuario, como si fueran parte del
 * manual, y no había forma de distinguir el chunk que realmente matcheó de sus
 * vecinos (que se agregan solo para dar continuidad de lectura).
 */
function parseSegments(text: string): Segment[] {
  if (!text.includes(MARKERS.relevant) && !text.includes(MARKERS.prev)) {
    return [{ role: "relevant", text: text.trim() }];
  }

  const segments: Segment[] = [];
  const pattern = new RegExp(
    `(${Object.values(MARKERS).map((m) => m.replace(/[[\]]/g, "\\$&")).join("|")})`,
  );
  const parts = text.split(pattern).filter((p) => p.trim());

  let role: Segment["role"] = "relevant";
  for (const part of parts) {
    if (part === MARKERS.prev) role = "prev";
    else if (part === MARKERS.relevant) role = "relevant";
    else if (part === MARKERS.next) role = "next";
    else segments.push({ role, text: part.trim() });
  }

  return segments.length ? segments : [{ role: "relevant", text: text.trim() }];
}

const ROLE_LABEL: Record<Segment["role"], string> = {
  prev: "contexto previo",
  relevant: "fragmento que matcheó",
  next: "contexto siguiente",
};

interface Props {
  text: string;
  /**
   * Omite el fragmento que matcheó, dejando solo el contexto previo/siguiente. Se usa
   * cuando ese fragmento ES una tabla y TableBlock ya la renderiza: el `document` de
   * un chunk de tabla es su markdown, así que mostrarlo acá además de la tabla era
   * el mismo contenido dos veces, con 20 líneas de pipes arriba de la tabla legible.
   */
  omitRelevant?: boolean;
}

export default function ChunkText({ text, omitRelevant }: Props) {
  const parsed = parseSegments(text);
  const segments = omitRelevant ? parsed.filter((s) => s.role !== "relevant") : parsed;
  const hasContext = segments.some((s) => s.role !== "relevant");

  if (segments.length === 0) return null;

  return (
    <div className="space-y-1.5">
      {segments.map((segment, i) => {
        const isRelevant = segment.role === "relevant";
        return (
          <div key={i}>
            {hasContext && (
              <p
                className={`mb-1 font-mono text-[0.6rem] uppercase tracking-wider ${
                  isRelevant ? "text-accent-2" : "text-muted-2"
                }`}
              >
                {ROLE_LABEL[segment.role]}
              </p>
            )}
            <pre
              className={`max-h-72 overflow-auto whitespace-pre-wrap rounded-lg border p-3 font-mono text-[0.72rem] leading-relaxed ${
                isRelevant
                  ? "border-accent-2/30 bg-accent-2-soft/40 text-text/95"
                  : "border-border bg-surface-2/60 text-muted"
              }`}
            >
              {segment.text}
            </pre>
          </div>
        );
      })}
    </div>
  );
}
