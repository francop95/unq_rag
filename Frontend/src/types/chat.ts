import type { Source } from "./api";

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  sources?: Source[];
  isError?: boolean;
  timestamp: number;
  /** Id de la ejecución en la API, para poder enviar feedback sobre esta respuesta. */
  queryId?: string;
}
