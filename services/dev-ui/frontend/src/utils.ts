export function pretty(value: unknown) {
  return JSON.stringify(value, null, 2);
}

export function parseJsonEditor<T>(raw: string, fallback: T): T {
  const trimmed = raw.trim();
  if (!trimmed) return fallback;
  return JSON.parse(trimmed) as T;
}

export function asErrorMessage(error: unknown, fallback = "Request failed.") {
  return error instanceof Error ? error.message : fallback;
}

export function formatList(values: unknown[] | undefined) {
  return values?.length ? values.map(String).join(", ") : "-";
}

export function statusTone(status: string | undefined) {
  const normalized = (status || "").toLowerCase();
  if (["ok", "completed", "success", "healthy"].includes(normalized)) return "good";
  if (["pending", "running", "started"].includes(normalized)) return "warn";
  if (["failed", "error", "cancelled"].includes(normalized)) return "bad";
  return "neutral";
}
