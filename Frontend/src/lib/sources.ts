import type { Source } from "../types/api";

export interface SourceSplit {
  /** Lo que el modelo declaró haber usado para responder. */
  citadas: Source[];
  /** Recuperado y entregado al modelo, pero que no declaró haber usado. */
  resto: Source[];
  /** Planos adjuntados siempre: tienen su propia sección, sin score. */
  planos: Source[];
  /**
   * `false` cuando el modelo no declaró ninguna fuente y `citadas` es, en
   * realidad, todo lo recuperado. Sirve para rotularlo distinto: una cosa es
   * "de acá salió la respuesta" y otra "esto es lo que tuvo a la vista".
   */
  declaradas: boolean;
}

/**
 * Separa las fuentes en lo que el modelo citó y lo que solo se le entregó.
 *
 * El retrieval trae 10 fragmentos y el panel los mostraba todos con el mismo
 * peso, así que una pregunta sobre la conexión del motor se ilustraba con la
 * tabla de inventario del secadero y dos figuras del manual del variador. Son
 * contexto recuperado, no respaldo: mostrarlos al mismo nivel que la figura de
 * la que salió la respuesta hace dudar de la respuesta entera.
 *
 * Cuando el modelo no cita nada no se esconde todo —eso dejaría la respuesta
 * sin ninguna referencia, que es justo cuando más falta hace— sino que se
 * muestra lo recuperado con `declaradas: false`.
 */
export function splitSources(sources: Source[]): SourceSplit {
  const planos = sources.filter((s) => s.is_attached_plan);
  const chunks = sources.filter((s) => !s.is_attached_plan);
  const citadas = chunks.filter((s) => s.citado);

  if (citadas.length > 0) {
    return { citadas, resto: chunks.filter((s) => !s.citado), planos, declaradas: true };
  }
  return { citadas: chunks, resto: [], planos, declaradas: false };
}
