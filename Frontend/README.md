# Asistente Técnico — Frontend

Interfaz de chat en React + TypeScript + Tailwind para el asistente de mantenimiento (`API/`). Muestra la respuesta del modelo junto con las fuentes citadas (documento + página + confianza) y la media asociada (tablas e imágenes/planos), obtenida de `Ingestion/data/media/` a través de la API.

## Requisitos

- Node 18+
- La API corriendo en `http://localhost:5000` (o la URL que configures)

## Uso

```bash
npm install
npm run dev
```

Abre `http://localhost:5173`.

## Configuración

`.env`:

```
VITE_API_BASE_URL=http://localhost:5000
```

## Notas

- Cada pregunta se envía de forma independiente (sin `conv_history`) para mantener el cliente simple; la API funciona igual sin ese campo, solo no dispara la lógica de *follow-up*. Si más adelante querés soporte de conversación multi-turno, hay que armar el historial en el formato que espera `API/utils/parser.py::parse_conv_history` y mandarlo en el body.
- Los `sources[].media[].media_path` que devuelve `/get_response` se resuelven contra `${VITE_API_BASE_URL}/media/<path>`, una ruta que sirve `Ingestion/data/media/` de solo lectura (agregada en `API/app.py`).
- El backend necesita CORS habilitado para que el navegador pueda llamarlo desde otro origen — ya está agregado en `API/app.py` (`CORS_ALLOWED_ORIGIN`, default `*` para desarrollo local; restringilo en producción).
