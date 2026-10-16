/**
 * Extract a human-readable error message from an unknown thrown value.
 * Backend FastAPI returns `{ detail: "..." }` for HTTPException and
 * `{ detail: [{loc, msg, type}, ...] }` for 422 validation errors.
 */
export function extractErrorMessage(err: unknown, fallback = '操作失败'): string {
  if (!err) return fallback;
  if (typeof err === 'string') return err;
  if (err instanceof Error) {
    const anyErr = err as Error & {
      response?: { data?: { detail?: unknown } };
      message?: string;
    };
    const detail = anyErr.response?.data?.detail;
    if (typeof detail === 'string' && detail) return detail;
    if (Array.isArray(detail) && detail.length > 0) {
      const first = detail[0] as { msg?: string; loc?: unknown[] };
      if (first?.msg) return first.msg;
    }
    return anyErr.message || fallback;
  }
  return fallback;
}
