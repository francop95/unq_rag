import type { MediaItem, Source } from "../types/api";

/** Una pieza de evidencia (imagen, plano o tabla) con la fuente de la que salió. */
export interface MediaEntry {
  media: MediaItem;
  path: string;
  fileName: string;
  page: string | number;
  /** Número que muestra el panel de fuentes (1-based), para poder referenciarla. */
  sourceNumber: number;
  kind: "image" | "table";
}

export const CONTENT_TYPE_LABEL: Record<string, string> = {
  table: "Tabla",
  image: "Imagen",
  diagram_visual: "Plano / Diagrama",
  diagram_text: "Diagrama (OCR)",
  diagram_description: "Diagrama (descripción)",
  superchunk: "Sección extendida",
  synthetic_question: "Texto",
  text: "Texto",
};

export function mediaPathOf(media: MediaItem): string | null {
  return media.media_path || media.image_path || null;
}

export function kindOf(media: MediaItem): "image" | "table" | null {
  const path = mediaPathOf(media);
  if (!path) return null;
  const type = (media.content_type || "").toLowerCase();
  if (type === "table" || path.endsWith(".json")) return "table";
  if (type === "image" || type === "diagram_visual" || /\.(png|jpe?g|webp|gif)$/i.test(path)) {
    return "image";
  }
  return null;
}

/**
 * Junta toda la evidencia visual de una respuesta en una sola lista, en el orden en
 * que se muestran las fuentes. Es lo que alimenta la galería y el visor: sin esto
 * cada imagen queda escondida detrás de su propia fila colapsada del panel y el
 * usuario no se entera de que la respuesta trae diagramas.
 */
export function collectMedia(sources: Source[]): MediaEntry[] {
  const entries: MediaEntry[] = [];
  const seen = new Set<string>();

  sources.forEach((source, i) => {
    (source.media || []).forEach((media) => {
      const path = mediaPathOf(media);
      const kind = kindOf(media);
      if (!path || !kind || seen.has(path)) return;
      seen.add(path);
      entries.push({
        media,
        path,
        kind,
        fileName: source.file_name,
        page: source.page,
        sourceNumber: i + 1,
      });
    });
  });

  return entries;
}

/** "3 diagramas · 2 tablas" — resumen corto de lo que trae la respuesta. */
export function summarizeMedia(entries: MediaEntry[]): string {
  const images = entries.filter((e) => e.kind === "image").length;
  const tables = entries.filter((e) => e.kind === "table").length;
  const parts: string[] = [];
  if (images) parts.push(`${images} ${images === 1 ? "imagen" : "imágenes"}`);
  if (tables) parts.push(`${tables} ${tables === 1 ? "tabla" : "tablas"}`);
  return parts.join(" · ");
}
