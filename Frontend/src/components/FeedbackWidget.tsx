import { useEffect, useState } from "react";
import { fetchFeedbackReasons, sendFeedback } from "../api/client";
import type { FeedbackReason } from "../types/api";

/**
 * Valoración de una respuesta, con motivos y comentario libre.
 *
 * El caso positivo es un clic y se acabó: pedirle motivos a quien está conforme
 * es la forma más rápida de que nadie vuelva a tocar el botón. Los motivos
 * aparecen solo al marcar que no sirvió, que es cuando hay algo que arreglar.
 *
 * Los motivos los trae la API. Duplicar la lista acá haría que una versión vieja
 * del frontend mandara etiquetas que el backend descarta, y ese feedback se
 * perdería sin que nadie se entere.
 */
export function FeedbackWidget({ queryId }: { queryId: string }) {
  const [motivosDisponibles, setMotivosDisponibles] = useState<FeedbackReason[]>([]);
  const [abierto, setAbierto] = useState(false);
  const [seleccionados, setSeleccionados] = useState<string[]>([]);
  const [comentario, setComentario] = useState("");
  const [estado, setEstado] = useState<"inicial" | "enviando" | "enviado" | "error">("inicial");

  useEffect(() => {
    let vigente = true;
    fetchFeedbackReasons()
      .then((m) => vigente && setMotivosDisponibles(m))
      // Si no se pueden traer, el pulgar arriba sigue andando: es mejor recoger
      // media señal que ninguna.
      .catch(() => undefined);
    return () => {
      vigente = false;
    };
  }, []);

  async function enviar(util: boolean, motivos: string[] = [], texto = "") {
    setEstado("enviando");
    try {
      await sendFeedback(queryId, util, motivos, texto);
      setEstado("enviado");
      setAbierto(false);
    } catch {
      setEstado("error");
    }
  }

  function alternar(id: string) {
    setSeleccionados((prev) =>
      prev.includes(id) ? prev.filter((m) => m !== id) : [...prev, id],
    );
  }

  if (estado === "enviado") {
    return <p className="feedback feedback--gracias">Gracias, quedó registrado.</p>;
  }

  return (
    <div className="feedback">
      {!abierto && (
        <div className="feedback__botones">
          <span className="feedback__pregunta">¿Te sirvió esta respuesta?</span>
          <button
            type="button"
            className="feedback__boton"
            onClick={() => enviar(true)}
            disabled={estado === "enviando"}
          >
            👍 Sí
          </button>
          <button
            type="button"
            className="feedback__boton"
            onClick={() => setAbierto(true)}
            disabled={estado === "enviando"}
          >
            👎 No
          </button>
        </div>
      )}

      {abierto && (
        <div className="feedback__detalle">
          <p className="feedback__pregunta">¿Qué falló? Podés marcar más de uno.</p>
          <ul className="feedback__motivos">
            {motivosDisponibles.map((m) => (
              <li key={m.id}>
                <label title={m.ayuda}>
                  <input
                    type="checkbox"
                    checked={seleccionados.includes(m.id)}
                    onChange={() => alternar(m.id)}
                  />
                  <span>{m.etiqueta}</span>
                </label>
              </li>
            ))}
          </ul>
          <textarea
            className="feedback__comentario"
            placeholder="Contanos qué esperabas (opcional pero muy útil)"
            value={comentario}
            onChange={(e) => setComentario(e.target.value)}
            rows={3}
          />
          <div className="feedback__acciones">
            <button
              type="button"
              className="feedback__boton feedback__boton--primario"
              onClick={() => enviar(false, seleccionados, comentario)}
              disabled={estado === "enviando"}
            >
              {estado === "enviando" ? "Enviando…" : "Enviar"}
            </button>
            <button
              type="button"
              className="feedback__boton"
              onClick={() => setAbierto(false)}
              disabled={estado === "enviando"}
            >
              Cancelar
            </button>
          </div>
          {estado === "error" && (
            <p className="feedback__error">No se pudo enviar. Probá de nuevo.</p>
          )}
        </div>
      )}
    </div>
  );
}
