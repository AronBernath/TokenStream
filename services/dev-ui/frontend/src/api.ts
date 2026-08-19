export type RequestOptions = RequestInit;

export async function apiFetch<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const headers = new Headers(options.headers || {});
  if (!headers.has("Cache-Control")) {
    headers.set("Cache-Control", "no-cache");
  }
  if (!headers.has("Pragma")) {
    headers.set("Pragma", "no-cache");
  }

  const response = await fetch(path, {
    credentials: "same-origin",
    ...options,
    cache: "no-store",
    headers,
  });

  if (!response.ok) {
    const text = await response.text();
    try {
      const payload = JSON.parse(text);
      const detail = payload.detail || payload.error || payload;
      if (Array.isArray(detail)) {
        const message = detail
          .map((item) => {
            const loc = Array.isArray(item?.loc) ? item.loc.join(".") : "";
            return [loc, item?.msg].filter(Boolean).join(": ");
          })
          .filter(Boolean)
          .join("; ");
        throw new Error(message || `Request failed with ${response.status}`);
      }
      const message = typeof detail === "string" ? detail : detail.error || detail.message;
      throw new Error(message || `Request failed with ${response.status}`);
    } catch (error) {
      if (error instanceof SyntaxError) {
        throw Object.assign(new Error(text || `Request failed with ${response.status}`), { cause: error });
      }
      throw error;
    }
  }

  if (response.status === 204) return null as T;
  return response.json() as Promise<T>;
}

export function authFetch<T>(path: string, options: RequestOptions = {}) {
  return apiFetch<T>(`/v1/auth/${path}`, options);
}

export function adminFetch<T>(path: string, options: RequestOptions = {}) {
  return apiFetch<T>(`/v1/management/${path}`, options);
}

export function jsonOptions(payload: unknown): RequestOptions {
  return {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  };
}
