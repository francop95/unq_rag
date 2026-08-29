import { useEffect, useRef, useState } from "react";
import Header, { type ApiStatus } from "./components/Header";
import ChatMessage from "./components/ChatMessage";
import TypingIndicator from "./components/TypingIndicator";
import QueryInput from "./components/QueryInput";
import WelcomeScreen from "./components/WelcomeScreen";
import { askQuestion, type HistoryTurn } from "./api/client";
import type { ChatMessage as ChatMessageT } from "./types/chat";

function newId(): string {
  return crypto.randomUUID();
}

export default function App() {
  const [messages, setMessages] = useState<ChatMessageT[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [apiStatus, setApiStatus] = useState<ApiStatus>("idle");
  const conversationId = useRef(newId());
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, loading]);

  async function sendQuery(query: string) {
    const trimmed = query.trim();
    if (!trimmed || loading) return;

    const userMessage: ChatMessageT = {
      id: newId(),
      role: "user",
      content: trimmed,
      timestamp: Date.now(),
    };
    setMessages((prev) => [...prev, userMessage]);
    setInput("");
    setLoading(true);
    setApiStatus("busy");

    try {
      // Historial de turnos completos (pregunta + respuesta) para que la API pueda
      // reescribir un follow-up como pregunta autónoma. Se arma desde `messages`, que
      // es el estado previo a este turno: el mensaje del usuario recién agregado no va.
      const history: HistoryTurn[] = [];
      for (let i = 0; i < messages.length - 1; i++) {
        const turn = messages[i];
        const reply = messages[i + 1];
        if (turn.role === "user" && reply.role === "assistant" && !reply.isError) {
          history.push({ question: turn.content, answer: reply.content });
        }
      }

      const results = await askQuestion(trimmed, conversationId.current, history);
      const first = results[0];

      const assistantMessage: ChatMessageT = {
        id: newId(),
        role: "assistant",
        content: first?.answer?.trim()
          ? first.answer
          : "No encontré información suficiente para responder con certeza. ¿Podés darme más detalle (código de falla, componente, página del manual)?",
        sources: first?.sources?.filter((s) => s.file_name),
        timestamp: Date.now(),
      };
      setMessages((prev) => [...prev, assistantMessage]);
      setApiStatus("ok");
    } catch (err) {
      setApiStatus("error");
      const message = err instanceof Error ? err.message : "Error desconocido";
      setMessages((prev) => [
        ...prev,
        {
          id: newId(),
          role: "assistant",
          content: `No pude conectarme con el servidor del asistente. (${message})`,
          isError: true,
          timestamp: Date.now(),
        },
      ]);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="blueprint-bg flex h-full flex-col bg-bg">
      <Header
        status={apiStatus}
        canReset={messages.length > 0 && !loading}
        onReset={() => {
          setMessages([]);
          setInput("");
          setApiStatus("idle");
          // Conversación nueva = id nuevo: el historial que se manda a la API se arma
          // desde `messages`, así que vaciarlo ya corta el follow-up.
          conversationId.current = newId();
        }}
      />

      <div ref={scrollRef} className="flex-1 overflow-y-auto">
        {messages.length === 0 ? (
          <WelcomeScreen onExample={sendQuery} />
        ) : (
          <div className="mx-auto flex max-w-3xl flex-col gap-5 px-4 py-6">
            {messages.map((m) => (
              <ChatMessage key={m.id} message={m} />
            ))}
            {loading && <TypingIndicator />}
          </div>
        )}
      </div>

      <QueryInput value={input} onChange={setInput} onSubmit={() => sendQuery(input)} disabled={loading} />
    </div>
  );
}
