import type { Source } from "../types/api";

export interface SourceSplit {
  /** Lo que el modelo declaró haber usado para responder. */
  citadas: Source[];
  /** Recuperado y entregado al modelo, pero que no declaró haber usado. */
  resto: Source[];
  /** Planos adjuntados siempre: tienen su propia sección, sin score. */
  planos: Source[];
  /**
   * `false` cuando el modelo no declaró ninguna fuente y `citadas` son, en
   * realidad, los fragmentos más relevantes de los que recibió. Sirve para
   * rotularlo distinto: una cosa es "de acá salió la respuesta" y otra "esto
   * es lo que tuvo a la vista".
   */
  declaradas: boolean;
}

/**
 * Cuántos fragmentos mostrar arriba cuando el modelo no declaró ninguno.
 *
 * Tres: suficientes para poder verificar la respuesta, pocos como para no
 * sugerir una precisión de atribución que no tenemos. El resto no se pierde,
 * queda detrás de "ver todo el contexto recuperado".
 */
const SIN_DECLARAR_ARRIBA = 3;

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
 * muestran los más relevantes con `declaradas: false`, y el resto se pliega
 * igual que siempre. La API manda los no citados ordenados por score, así que
 * "los más relevantes" son los primeros.
 */
export function splitSources(sources: Source[]): SourceSplit {
  // Los planos se adjuntan a TODA consulta, también a las que no tienen nada
  // que ver con ellos. El que el modelo no declaró haber leído no va a la
  // sección de planos: baja al pliegue con el resto del contexto, porque
  // mostrarlo arriba afirma que de ahí salió algo.
  const planos = sources.filter((s) => s.is_attached_plan && s.citado);
  const planosSinUsar = sources.filter((s) => s.is_attached_plan && !s.citado);
  const chunks = sources.filter((s) => !s.is_attached_plan);
  const citadas = chunks.filter((s) => s.citado);

  if (citadas.length > 0) {
    return {
      citadas,
      resto: [...chunks.filter((s) => !s.citado), ...planosSinUsar],
      planos,
      declaradas: true,
    };
  }
  return {
    citadas: chunks.slice(0, SIN_DECLARAR_ARRIBA),
    resto: [...chunks.slice(SIN_DECLARAR_ARRIBA), ...planosSinUsar],
    planos,
    declaradas: false,
  };
}
