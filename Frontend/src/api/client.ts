import type { ApiResponse, ApiResult, TableMedia } from "../types/api";

export const API_BASE_URL: string =
  (import.meta.env.VITE_API_BASE_URL as string) || "http://localhost:5000";

/** Un turno anterior de la conversación, en el formato que espera parse_conv_history. */
export interface HistoryTurn {
  question: string;
  answer: string;
}

/**
 * Historial en el formato anidado que espera `parse_conv_history` de la API.
 * Los campos `files` y `like` existen en ese contrato aunque este cliente no los use:
 * el parser los lee posicionalmente y sin ellos tira excepción.
 */
function buildConvHistory(history: HistoryTurn[]) {
  return history.map((turn) => ({
    question: turn.question,
    answers: [{ answer: turn.answer, files: [], like: null }],
  }));
}

/**
 * Envía una pregunta al asistente, con los turnos anteriores para que el backend pueda
 * resolver los follow-ups.
 *
 * El historial no se le pasa al LLM que responde: la API clasifica la intención y, si es
 * un follow-up, reescribe la pregunta como autónoma ANTES del retrieval. Eso es lo que
 * hace que "¿y si eso no funciona?" recupere algo — buscar ese vector tal cual no
 * recupera nada.
 */
export async function askQuestion(
  query: string,
  conversationId: string,
  history: HistoryTurn[] = [],
): Promise<ApiResult[]> {
  const res = await fetch(`${API_BASE_URL}/get_response`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      query,
      conversation_id: conversationId,
      message_id: crypto.randomUUID(),
      // Solo los turnos más recientes: PREV_CONV_THRESHOLD del backend mira 1, y mandar
      // toda la conversación solo agrega tokens al clasificador de intención.
      ...(history.length ? { conv_history: buildConvHistory(history.slice(-3)) } : {}),
    }),
  });

  if (!res.ok) {
    throw new Error(`El servidor respondió ${res.status} ${res.statusText}`);
  }

  const data: ApiResponse = await res.json();
  return data.Results ?? [];
}

/**
 * Construye la URL pública de un archivo de media (imagen/tabla) servido por la API.
 * Codifica cada segmento por separado: los nombres reales traen espacios
 * ("Tesis 06-2025.docx") y podrían traer `#` o `?`, que sin codificar cortarían la
 * URL. Se codifica acá y en un solo lugar, así que quien la llame pasa el path crudo.
 */
export function mediaUrl(mediaPath: string): string {
  const cleanPath = mediaPath.replace(/^\/+/, "");
  const encoded = cleanPath.split("/").map(encodeURIComponent).join("/");
  return `${API_BASE_URL}/media/${encoded}`;
}

/**
 * URL del PDF original (Ingestion/data/raw_data/<file_name>) con fragmento #page=N.
 * Chrome/Firefox/Edge abren el visor nativo de PDF en esa página; Safari lo abre
 * igual pero puede ignorar el fragmento y arrancar en la página 1.
 */
export function sourceDocumentUrl(fileName: string, page: string | number): string {
  // El número de página puede venir como rango de superchunk ("82-82"): el visor de
  // PDF necesita una página sola, así que se toma la primera.
  const firstPage = String(page).split("-")[0] || "1";
  return `${mediaUrl(`raw_data/${fileName}`)}#page=${firstPage}`;
}

/** Descarga y parsea el JSON de una tabla guardada por Ingestion (MultimodalStorage). */
export async function fetchTableMedia(mediaPath: string): Promise<TableMedia> {
  const res = await fetch(mediaUrl(mediaPath));
  if (!res.ok) {
    throw new Error(`No se pudo cargar la tabla (${res.status})`);
  }
  return res.json();
}
