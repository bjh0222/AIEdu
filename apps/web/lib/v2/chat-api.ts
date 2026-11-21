"use client";

// Reader chat uses the existing backend chat route. The backend—not the browser—
// owns model selection, provider calls and ModelCallLog accounting.
export type ChatScope = "current_unit" | "whole_document" | "selected_units";

export type ChatContextUnit = {
  unit_id: string;
  title?: string;
  body: string;
};

export type ChatMessageView = {
  id: string;
  role: "user" | "assistant";
  content: string;
  quote?: string;
  created_at?: string;
};

export type ChatSendInput = {
  task_id: string;
  artifact_id?: string | null;
  question: string;
  scope: ChatScope;
  context_units: ChatContextUnit[];
  selected_quote?: string | null;
};

export type ChatSendResult = {
  assistant: { id: string; content: string };
  model_call?: {
    provider: string;
    model: string;
    call_id?: string | null;
    usage?: { prompt_tokens?: number; completion_tokens?: number; total_tokens?: number; usage_source?: string };
  } | null;
};

export async function sendReaderChat(input: ChatSendInput, signal?: AbortSignal): Promise<ChatSendResult> {
  const response = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    cache: "no-store",
    signal,
    body: JSON.stringify({
      task_id: input.task_id,
      artifact_id: input.artifact_id ?? null,
      question: input.question,
      context_scope: input.scope,
      context_units: input.context_units,
      selected_quote: input.selected_quote ?? null,
    }),
  });
  const body = (await response.json().catch(() => ({}))) as Record<string, unknown>;
  if (!response.ok) {
    const detail = body.detail;
    const code = typeof body.code === "string" ? body.code
      : detail && typeof detail === "object" && typeof (detail as Record<string, unknown>).code === "string"
        ? String((detail as Record<string, unknown>).code)
        : `HTTP_${response.status}`;
    const message = detail && typeof detail === "object" && typeof (detail as Record<string, unknown>).message === "string"
      ? String((detail as Record<string, unknown>).message)
      : typeof detail === "string" ? detail : code;
    throw new Error(message);
  }
  const assistant = body.assistant as { id?: string; content?: string } | undefined;
  if (!assistant || typeof assistant.content !== "string") {
    throw new Error("聊天响应缺少 assistant.content");
  }
  return {
    assistant: { id: String(assistant.id ?? crypto.randomUUID()), content: assistant.content },
    model_call: (body.model_call as ChatSendResult["model_call"]) ?? null,
  };
}
