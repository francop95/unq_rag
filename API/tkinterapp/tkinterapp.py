import os
import json
import uuid
import queue
import threading
import webbrowser
import tkinter as tk
from tkinter import ttk, messagebox
import requests
import urllib.parse
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv, find_dotenv
env_file = find_dotenv(filename=".env")  # busca desde CWD hacia padres
load_dotenv(env_file, override=False)

API_URL = os.getenv("API_URL", "http://127.0.0.1:5000/get_response")  # <-- ajusta
API_KEY = os.getenv("API_KEY", "")

# Para links
DOC_BASE_URL = os.getenv("DOC_BASE_URL", "").rstrip("/")  # p. ej. https://tu-dominio/static/docs
DOC_BASE_DIR = os.getenv("DOC_BASE_DIR", "").rstrip("/")  # p. ej. /ruta/local/a/pdfs

TIMEOUT = (10, 240)  # (connect, read)

def open_url(url: str):
    """Abre URL en una pestaña nueva. Acepta http(s) o file://"""
    if not url:
        messagebox.showerror("Error", "URL vacía o no válida")
        return
    
    try:
        webbrowser.open(url, new=2)
    except Exception as e:
        messagebox.showerror("Error al abrir enlace", f"No pude abrir el enlace:\n{url}\n\nDetalle: {e}")

# Mapeo de nombres de archivos para normalizar inconsistencias
FILE_NAME_MAPPING = {
    "plano_distribucion_electrica.pdf": "Plano distribucion electrica.pdf",
}

def normalize_file_name(file_name: str) -> str:
    """Normaliza nombres de archivos usando el mapeo definido"""
    return FILE_NAME_MAPPING.get(file_name, file_name)

def build_page_link(file_name: Optional[str], page: Optional[int]) -> Optional[str]:
    """
    Devuelve un link navegable a la página del PDF:
      - Si hay DOC_BASE_URL:  https://.../archivo.pdf#page=N
      - Si hay DOC_BASE_DIR:  file:///ruta/absoluta/archivo.pdf#page=N
      - Si no, None
    """
    if not file_name:
        return None

    # Normalizar el nombre del archivo
    file_name = normalize_file_name(file_name)

    # Lee de globals si existen; sino, de env
    DOC_BASE_URL = globals().get("DOC_BASE_URL") or os.getenv("DOC_BASE_URL")
    DOC_BASE_DIR = globals().get("DOC_BASE_DIR") or os.getenv("DOC_BASE_DIR")

    # Normaliza la página
    frag = ""
    try:
        if page is not None:
            p = int(page)
            if p > 0:
                frag = f"#page={p}"
    except (TypeError, ValueError):
        pass  # sin fragmento si page inválido

    # URL (servido por web)
    if DOC_BASE_URL:
        base = DOC_BASE_URL.rstrip("/")
        fname = urllib.parse.quote(file_name)
        return f"{base}/{fname}{frag}"

    # Ruta local (file://)
    if DOC_BASE_DIR:
        base_dir = Path(DOC_BASE_DIR)
        path = (base_dir / file_name).resolve()
        # Verificar que el archivo existe
        if path.exists():
            return f"file://{path}{frag}"
        else:
            print(f"Archivo no encontrado: {path}")
            return None

    return None

# =========================
# UI
# =========================
class App(ttk.Frame):
    def __init__(self, master):
        super().__init__(master, padding=20)
        self.master.title("Asistente de Mantenimiento · Secadora de Pasta")
        self.master.geometry("1000x700")
        self.master.minsize(800, 600)
        self._setup_style()
        self._build_header()
        self._build_body()

    def _setup_style(self):
        style = ttk.Style()

        # Paleta moderna y atractiva
        self.bg = "#0a0e27"         # Azul oscuro profundo
        self.card = "#1a1f3a"       # Azul oscuro para cards
        self.accent = "#00d4ff"     # Cyan brillante
        self.accent_hover = "#00e8ff"  # Cyan más brillante al pasar
        self.text = "#e8eef7"       # Blanco grisáceo
        self.subtle = "#8892b0"     # Gris azulado
        self.badge_bg = "#253549"   # Azul oscuro para badges
        self.border = "#2a3f5f"     # Borde azulado
        self.success = "#00d084"    # Verde para éxito

        self.master.configure(bg=self.bg)
        
        # Estilos base
        style.configure("TFrame", background=self.bg)
        style.configure("Card.TFrame", background=self.card, relief="flat")
        style.configure("Badge.TLabel", background=self.badge_bg, foreground=self.accent, padding=(10, 4), font=("SF Pro Text", 10, "bold"))
        
        # Títulos y textos con mejor jerarquía
        style.configure("Title.TLabel", background=self.bg, foreground=self.accent, font=("SF Pro Display", 22, "bold"))
        style.configure("Hint.TLabel", background=self.bg, foreground=self.subtle, font=("SF Pro Text", 12))
        style.configure("CardTitle.TLabel", background=self.card, foreground=self.accent, font=("SF Pro Text", 13, "bold"))
        style.configure("Body.TLabel", background=self.card, foreground=self.text, font=("SF Pro Text", 11), wraplength=750)
        style.configure("Meta.TLabel", background=self.card, foreground=self.subtle, font=("SF Pro Text", 10))
        style.configure("Link.TLabel", background=self.card, foreground=self.accent, cursor="hand2", font=("SF Pro Text", 10, "underline"))
        
        # Botones mejorados
        style.configure("TButton", padding=(14, 10), font=("SF Pro Text", 11))
        style.configure("Accent.TButton", background=self.accent, foreground="#0a0e27", font=("SF Pro Text", 11, "bold"), relief="flat", borderwidth=0)
        style.map("Accent.TButton",
                  background=[("active", self.accent_hover), ("pressed", "#00b8e6")],
                  foreground=[("active", "#0a0e27")])
        
        # Input mejorado
        style.configure("Search.TEntry", fieldbackground="#141829", foreground=self.text, padding=12, font=("SF Pro Text", 11), relief="flat", borderwidth=1)
        style.map("Search.TEntry", 
                  fieldbackground=[("focus", "#1a1f3a")],
                  bordercolor=[("focus", self.accent)])

    def _build_header(self):
        header = ttk.Frame(self, style="TFrame")
        header.pack(fill="x", pady=(0, 20), padx=0)

        title = ttk.Label(header, text="🔍 Asistente", style="Title.TLabel")
        title.pack(side="left", anchor="w")

        hint = ttk.Label(header, text="Busca respuestas en los manuales técnicos", style="Hint.TLabel")
        hint.pack(side="left", padx=(16, 0), anchor="w")

        self.pack(fill="both", expand=True)

    def _open_planos_dialog(self):
        """Abre un diálogo para seleccionar qué planos abrir."""
        planos_electricos_path = os.getenv("PLANOS_ELECTRICOS_URL", "")
        planos_conexionado_path = os.getenv("PLANOS_CONEXIONADO_URL", "")

        if not planos_electricos_path or not planos_conexionado_path:
            messagebox.showerror("Error", "No se han configurado las rutas de los planos.")
            return

        # Convertir rutas locales a file:// URLs
        def path_to_file_url(path: str) -> str:
            if path.startswith("file://"):
                return path
            return f"file://{Path(path).resolve()}"

        planos_electricos_url = path_to_file_url(planos_electricos_path)
        planos_conexionado_url = path_to_file_url(planos_conexionado_path)

        # Crear ventana de diálogo personalizada
        dialog = tk.Toplevel(self.master)
        dialog.title("Seleccionar planos")
        dialog.geometry("480x240")
        dialog.resizable(False, False)
        dialog.grab_set()
        dialog.configure(bg=self.bg)

        # Centrar la ventana
        dialog.transient(self.master)
        dialog.update_idletasks()
        x = self.master.winfo_x() + (self.master.winfo_width() // 2) - (dialog.winfo_width() // 2)
        y = self.master.winfo_y() + (self.master.winfo_height() // 2) - (dialog.winfo_height() // 2)
        dialog.geometry(f"+{x}+{y}")

        # Contenido
        frame = ttk.Frame(dialog, padding=20, style="TFrame")
        frame.pack(fill="both", expand=True)

        ttk.Label(frame, text="📑 ¿Qué planos deseas abrir?", style="CardTitle.TLabel").pack(anchor="w", pady=(0, 18))

        # Botones - distribuir en dos filas
        buttons_frame = ttk.Frame(frame, style="TFrame")
        buttons_frame.pack(fill="x", pady=(16, 0))

        row1 = ttk.Frame(buttons_frame, style="TFrame")
        row1.pack(fill="x", pady=(0, 10))

        ttk.Button(
            row1,
            text="⚡ Diagrama Potencia",
            command=lambda: (open_url(planos_electricos_url), dialog.destroy()),
            style="Accent.TButton"
        ).pack(side="left", padx=(0, 8))

        ttk.Button(
            row1,
            text="🔌 Diagrama TBEN",
            command=lambda: (open_url(planos_conexionado_url), dialog.destroy()),
            style="Accent.TButton"
        ).pack(side="left")

        row2 = ttk.Frame(buttons_frame, style="TFrame")
        row2.pack(fill="x")

        ttk.Button(
            row2,
            text="Cancelar",
            command=dialog.destroy
        ).pack(side="left")

    def _build_body(self):
        # Búsqueda con mejor styling
        search_bar = ttk.Frame(self, style="TFrame")
        search_bar.pack(fill="x", pady=(0, 18), padx=2)

        self.query_var = tk.StringVar()
        entry = ttk.Entry(search_bar, textvariable=self.query_var, style="Search.TEntry")
        entry.pack(side="left", fill="x", expand=True, padx=(0, 10))
        entry.bind("<Return>", lambda e: self.on_search())

        ask_btn = ttk.Button(search_bar, text="✨ Preguntar", command=self.on_search, style="Accent.TButton")
        ask_btn.pack(side="left")

        # Área scrollable
        container = ttk.Frame(self, style="TFrame")
        container.pack(fill="both", expand=True, padx=0)

        self.canvas = tk.Canvas(container, bd=0, highlightthickness=0, bg=self.bg)
        self.canvas.pack(side="left", fill="both", expand=True)

        scrollbar = ttk.Scrollbar(container, orient="vertical", command=self.canvas.yview)
        scrollbar.pack(side="right", fill="y", padx=(8, 0))

        self.canvas.configure(yscrollcommand=scrollbar.set)
        self.result_frame = ttk.Frame(self.canvas, style="TFrame")
        self.canvas_window = self.canvas.create_window((0, 0), window=self.result_frame, anchor="nw")

        self.result_frame.bind("<Configure>", self._on_frame_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        
        # Permitir scroll con la rueda del ratón en macOS
        # Bind a nivel del master window para capturar eventos globales
        self.master.bind("<MouseWheel>", self._on_mouse_wheel_wrapper)
        self.master.bind("<Button-4>", self._on_mouse_wheel_wrapper)
        self.master.bind("<Button-5>", self._on_mouse_wheel_wrapper)

        # Mensaje inicial
        self._empty_state()

    def _on_mouse_wheel_wrapper(self, event):
        """Wrapper que verifica si el mouse está sobre el canvas antes de scrollear"""
        # Obtener posición del mouse
        x = event.x_root
        y = event.y_root
        
        # Obtener posición del canvas
        canvas_x = self.canvas.winfo_rootx()
        canvas_y = self.canvas.winfo_rooty()
        canvas_w = self.canvas.winfo_width()
        canvas_h = self.canvas.winfo_height()
        
        # Verificar si el mouse está sobre el canvas
        if canvas_x <= x <= canvas_x + canvas_w and canvas_y <= y <= canvas_y + canvas_h:
            self._on_mouse_wheel(event)

    def _empty_state(self):
        for w in self.result_frame.winfo_children():
            w.destroy()
        card = self._card(self.result_frame)
        ttk.Label(card, text="📚 Bienvenido", style="CardTitle.TLabel").pack(anchor="w", pady=(0, 10))
        ttk.Label(card, text="Escribe una pregunta arriba para encontrar respuestas en los manuales técnicos.", style="Body.TLabel", justify="left", wraplength=750).pack(anchor="w")

    def _on_frame_configure(self, _):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_configure(self, event):
        self.canvas.itemconfig(self.canvas_window, width=event.width)

    def _on_mouse_wheel(self, event):
        """Maneja el scroll con la rueda del ratón"""
        try:
            # En macOS/Windows: event.delta
            if hasattr(event, 'delta') and event.delta:
                if event.delta > 0:
                    self.canvas.yview_scroll(-5, "units")
                else:
                    self.canvas.yview_scroll(5, "units")
            # En Linux: event.num
            elif hasattr(event, 'num'):
                if event.num == 4:
                    self.canvas.yview_scroll(-5, "units")
                elif event.num == 5:
                    self.canvas.yview_scroll(5, "units")
        except Exception as e:
            print(f"Error en scroll: {e}")
        
        return "break"

    # =========================
    # Lógica de búsqueda
    # =========================
    def on_search(self):
        q = self.query_var.get().strip()
        if not q:
            messagebox.showinfo("Consulta", "Escribe una pregunta.")
            return
        self._render_loading()
        self.master.after(50, lambda: self._do_request(q))

    def _render_loading(self):
        for w in self.result_frame.winfo_children():
            w.destroy()
        card = self._card(self.result_frame)
        ttk.Label(card, text="⏳ Buscando respuesta…", style="Body.TLabel").pack(anchor="w")

    def _do_request(self, query: str):
        try:
            # Ajusta el payload según tu API
            payload = {"query": query}
            r = requests.post(API_URL, json=payload, timeout=240)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            self._render_error(str(e))
            return

        try:
            results = data.get("Results") or data.get("results") or []
            self._render_results(results, original=data)
        except Exception as e:
            self._render_error(f"Error interpretando la respuesta: {e}\n{json.dumps(data, ensure_ascii=False, indent=2)}")

    def _render_error(self, msg: str):
        for w in self.result_frame.winfo_children():
            w.destroy()
        card = self._card(self.result_frame)
        ttk.Label(card, text="❌ Ocurrió un error", style="CardTitle.TLabel").pack(anchor="w")
        ttk.Label(card, text=msg, style="Body.TLabel", wraplength=780).pack(anchor="w", pady=(8, 0))

    def _card(self, parent) -> ttk.Frame:
        outer = ttk.Frame(parent, style="TFrame")
        outer.pack(fill="x", padx=4, pady=8)
        # Borde superior con gradiente visual
        border = tk.Frame(outer, bg=self.accent, height=2)
        border.pack(fill="x", side="top")
        # Card con padding mejorado
        card = ttk.Frame(outer, style="Card.TFrame", padding=18)
        card.pack(fill="x")
        return card

    def _render_results(self, results: list[dict], original: dict | None = None):
        for w in self.result_frame.winfo_children():
            w.destroy()

        if not results:
            self._empty_state()
            return

        for idx, res in enumerate(results, start=1):
            self._render_result_card(res, idx)

    def _render_result_card(self, res: dict, idx: int):
        card = self._card(self.result_frame)

        # Header mejorado
        header = ttk.Frame(card, style="Card.TFrame")
        header.pack(fill="x", pady=(0, 12))
        ttk.Label(header, text="💡 Respuesta", style="CardTitle.TLabel").pack(side="left")

        # Pregunta (meta)
        q = res.get("question") or ""
        if q:
            ttk.Label(card, text=f"🔍 Pregunta original: {q}", style="Meta.TLabel", wraplength=750).pack(anchor="w", pady=(0, 10))

        # Respuesta (cuerpo mejorado)
        answer = res.get("answer") or "(sin respuesta)"
        ans_lbl = ttk.Label(card, text=answer, style="Body.TLabel", wraplength=750, justify="left")
        ans_lbl.pack(anchor="w", pady=(0, 12))

        # Archivos / páginas
        files = res.get("files") or []
        
        if files:
            links_frame = ttk.Frame(card, style="Card.TFrame")
            links_frame.pack(fill="x", pady=(12, 0))

            # Frame para botones lado a lado
            buttons_row = ttk.Frame(links_frame, style="Card.TFrame")
            buttons_row.pack(anchor="w", pady=(0, 10))

            # Botón de abrir todas
            all_links = []
            for f in files:
                fname = f.get("file_name")
                pages = f.get("pages") or []
                for p in pages:
                    all_links.append(build_page_link(fname, p))

            if any(all_links):
                ttk.Button(buttons_row, text="📖 Abrir todas", command=lambda ls=all_links: [open_url(u) for u in ls if u], style="Accent.TButton").pack(side="left", padx=(0, 8))

            # Botón de planos eléctricos
            ttk.Button(buttons_row, text="📋 Planos", command=self._open_planos_dialog, style="Accent.TButton").pack(side="left")

            # Cada archivo
            for f in files:
                self._render_file_block(links_frame, f)

    def _render_file_block(self, parent, file_item: dict):
        block = ttk.Frame(parent, style="Card.TFrame")
        
        fname = file_item.get("file_name")
        if not fname:
            return  # No mostrar si no hay nombre

        ttk.Label(block, text=fname, style="Meta.TLabel").pack(anchor="w")
        block.pack(fill="x", pady=(6, 8))

        pages = list(dict.fromkeys(file_item.get("pages") or []))  # único y orden estable
        if not pages:
            # Si no hay páginas, no mostrar nada más
            return

        # Badges de páginas
        pages_row = ttk.Frame(block, style="Card.TFrame")
        pages_row.pack(anchor="w", pady=(4, 4))
        ttk.Label(pages_row, text="Páginas:", style="Meta.TLabel").pack(side="left", padx=(0, 6))
        for p in pages:
            b = ttk.Label(pages_row, text=str(p), style="Badge.TLabel")
            b.pack(side="left", padx=3)

    def _link_label(self, parent, url: str | None):
        if not url:
            return  # No mostrar nada si no hay URL válida
        
        link_lbl = ttk.Label(parent, text=url, style="Link.TLabel", wraplength=780, justify="left")
        link_lbl.pack(anchor="w", pady=(2, 0))
        link_lbl.bind("<Button-1>", lambda e, u=url: open_url(u))

# =========================
# Main
# =========================
def main():
    root = tk.Tk()
    app = App(root)
    root.mainloop()

if __name__ == "__main__":
    main()