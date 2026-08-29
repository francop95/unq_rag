export interface MediaItem {
  content_type: string | null;
  media_path: string | null;
  image_path: string | null;
}

export interface Source {
  file_name: string;
  page: string | number;
  similarity_score: number;
  media: MediaItem[];
  text?: string;
  /**
   * Plano adjuntado siempre al LLM (no recuperado del índice por similitud), así
   * que no tiene score ni chunk asociado: se muestra solo como referencia abrible.
   */
  is_attached_plan?: boolean;
}

export interface ApiResult {
  question?: string;
  answer: string;
  similarity_score: number;
  ans_type: string;
  is_valid?: boolean;
  sources?: Source[];
  files?: { file_name: string; pages: (string | number)[] }[];
}

export interface ApiResponse {
  Results: ApiResult[];
}

export interface TableMedia {
  markdown?: string;
  json?: { rows?: (string | number | null)[][] };
  searchable_text?: string;
  bbox?: number[];
}
