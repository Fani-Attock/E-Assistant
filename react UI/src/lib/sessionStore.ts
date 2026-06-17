import type { ConversationSummary } from "../types/chat";

const SESSIONS_KEY = "eassistant_recent_sessions_v1";
const USER_ID_KEY = "eassistant_user_id_v1";
const LEGACY_SESSIONS_KEY = "shopmind_recent_sessions_v1";
const LEGACY_USER_ID_KEY = "shopmind_user_id_v1";

function safeParse(value: string | null): ConversationSummary[] {
  if (!value) {
    return [];
  }
  try {
    const parsed = JSON.parse(value);
    if (!Array.isArray(parsed)) {
      return [];
    }
    return parsed.filter((row) => row && typeof row === "object") as ConversationSummary[];
  } catch {
    return [];
  }
}

export function listRecentSessions(): ConversationSummary[] {
  const primary = safeParse(window.localStorage.getItem(SESSIONS_KEY));
  const legacy = safeParse(window.localStorage.getItem(LEGACY_SESSIONS_KEY));
  const merged = [...primary, ...legacy];
  const dedup = merged.filter(
    (row, idx) => merged.findIndex((x) => x.conversationId === row.conversationId) === idx
  );
  return dedup.sort((a, b) => b.timestamp - a.timestamp);
}

export function upsertRecentSession(session: ConversationSummary): void {
  const rows = listRecentSessions();
  const without = rows.filter((x) => x.conversationId !== session.conversationId);
  const next = [session, ...without].slice(0, 100);
  window.localStorage.setItem(SESSIONS_KEY, JSON.stringify(next));
}

export function removeRecentSession(conversationId: string): void {
  const next = listRecentSessions().filter((x) => x.conversationId !== conversationId);
  window.localStorage.setItem(SESSIONS_KEY, JSON.stringify(next));
}

export function clearRecentSessions(): void {
  window.localStorage.removeItem(SESSIONS_KEY);
}

function createUserId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return `ui_${crypto.randomUUID().replace(/-/g, "").slice(0, 16)}`;
  }
  const fallback = Math.random().toString(36).slice(2, 12);
  return `ui_${fallback}`;
}

export function getOrCreateUserId(): string {
  const existing = window.localStorage.getItem(USER_ID_KEY) || window.localStorage.getItem(LEGACY_USER_ID_KEY);
  if (existing && existing.trim()) {
    if (!window.localStorage.getItem(USER_ID_KEY)) {
      window.localStorage.setItem(USER_ID_KEY, existing.trim());
    }
    return existing.trim();
  }
  const next = createUserId();
  window.localStorage.setItem(USER_ID_KEY, next);
  return next;
}
