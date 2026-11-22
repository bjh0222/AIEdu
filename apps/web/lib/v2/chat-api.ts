"use client";

// Reader chat uses the v2 backend chat route (/api/v2/chat，secured：会话+CSRF+幂等键)。
// The backend—not the browser—owns model selection, provider calls and ModelCallLog accounting.
import { act, explain, newKey, UnknownOutcomeError } from "./client";

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
  const key = newKey();
  try {
    const body = await act<Record<string, unknown>>("POST", "/chat", {
      task_id: input.task_id,
      artifact_id: input.artifact_id ?? null,
      question: input.question,
      context_scope: input.scope,
      context_units: input.context_units,
      selected_quote: input.selected_quote ?? null,
    }, { kind: "reader_chat", key }, { signal });
    const assistant = body.assistant as { id?: string; content?: string } | undefined;
    if (!assistant || typeof assistant.content !== "string") {
      throw new Error("聊天响应缺少 assistant.content");
    }
    return {
      assistant: { id: String(assistant.id ?? crypto.randomUUID()), content: assistant.content },
      model_call: (body.model_call as ChatSendResult["model_call"]) ?? null,
    };
  } catch (e) {
    if (e instanceof UnknownOutcomeError) throw e;
    throw new Error(explain(e));
  }
}
