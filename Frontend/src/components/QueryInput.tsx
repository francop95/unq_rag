import { useEffect, useRef, type KeyboardEvent } from "react";

interface Props {
  value: string;
  onChange: (v: string) => void;
  onSubmit: () => void;
  disabled?: boolean;
}

export default function QueryInput({ value, onChange, onSubmit, disabled }: Props) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Crece con el contenido hasta el máximo del contenedor. Una descripción de falla
  // suele ser de 2-3 líneas y con una sola visible había que scrollear dentro del
  // input para releer lo que se escribió.
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 128)}px`; // 128px = max-h-32 de abajo
  }, [value]);

  function handleKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      if (value.trim() && !disabled) onSubmit();
    }
  }

  return (
    <div className="shrink-0 border-t border-border bg-surface/80 p-4 backdrop-blur">
      <div className="mx-auto flex max-w-3xl items-end gap-2 rounded-xl border border-border bg-surface-2 p-2 transition-colors focus-within:border-accent-2/50 focus-within:shadow-lg focus-within:shadow-accent-2/5">
        <textarea
          ref={textareaRef}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={handleKeyDown}
          disabled={disabled}
          rows={1}
          placeholder="Describí la falla, componente o consulta técnica…"
          className="max-h-32 min-h-[2.25rem] flex-1 resize-none bg-transparent px-2 py-1.5 text-sm text-text placeholder:text-muted-2 focus:outline-none disabled:opacity-60"
        />
        <button
          type="button"
          onClick={onSubmit}
          disabled={disabled || !value.trim()}
          className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-accent text-bg transition enabled:hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-40"
          aria-label="Enviar pregunta"
        >
          <svg viewBox="0 0 24 24" fill="none" className="h-4 w-4">
            <path
              d="M4 12h15M13 5l7 7-7 7"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        </button>
      </div>
      <p className="mx-auto mt-2 max-w-3xl font-mono text-[0.65rem] text-muted-2">
        Enter para enviar · Shift+Enter para salto de línea
      </p>
    </div>
  );
}
