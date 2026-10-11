import { HttpErrorResponse } from '@angular/common/http';

import type { ApiError } from '../api/types';

/** Human-readable message from a failed API call (FastAPI's `detail`), for display. */
export function errorMessage(err: unknown): string {
  if (err instanceof HttpErrorResponse) {
    const detail = (err.error as ApiError | null)?.detail;
    if (typeof detail === 'string') return detail;
    if (Array.isArray(detail)) return detail.map((d) => d.msg).join('; '); // 422 validation
    if (err.status === 0) return 'Cannot reach the server.';
  }
  return 'Something went wrong. Please try again.';
}
