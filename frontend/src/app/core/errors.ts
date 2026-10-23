import { HttpErrorResponse } from '@angular/common/http';

import type { ApiError } from '../api/types';
import { humanize } from './format';

/** "Monthly queries: input should be greater than or equal to 0" (QA: the bare pydantic
 *  message didn't say which field was wrong). */
function describe(d: { msg: string; loc?: (string | number)[] }): string {
  const field = d.loc?.filter((part) => typeof part === 'string' && part !== 'body').at(-1);
  if (!field) return d.msg;
  return `${humanize(String(field))}: ${d.msg.charAt(0).toLowerCase()}${d.msg.slice(1)}`;
}

/** Human-readable message from a failed API call (FastAPI's `detail`), for display. */
export function errorMessage(err: unknown): string {
  if (err instanceof HttpErrorResponse) {
    const detail = (err.error as ApiError | null)?.detail;
    if (typeof detail === 'string') return detail;
    if (Array.isArray(detail)) return detail.map(describe).join('; '); // 422 validation
    if (err.status === 0) return 'Cannot reach the server.';
  }
  return 'Something went wrong. Please try again.';
}
