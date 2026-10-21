import { HttpErrorResponse, HttpInterceptorFn } from '@angular/common/http';
import { inject } from '@angular/core';
import { catchError, throwError } from 'rxjs';

import { Auth } from './auth';

/**
 * Adds the bearer token and the selected org (X-Org-Id) to every API call, in one place,
 * so components never handle credentials. A 401 means the token expired or the account was
 * disabled, so the user is sent back to the login page. The customer chat (/v1/chat) carries
 * its own customer token and never gets staff credentials.
 */
export const apiInterceptor: HttpInterceptorFn = (req, next) => {
  if (!req.url.startsWith('/v1/') || req.url.startsWith('/v1/chat')) return next(req);

  const auth = inject(Auth);
  const token = auth.token();
  const orgId = auth.orgId();
  const headers: Record<string, string> = {};
  if (token) headers['Authorization'] = `Bearer ${token}`;
  if (orgId) headers['X-Org-Id'] = orgId;

  return next(req.clone({ setHeaders: headers })).pipe(
    catchError((err: unknown) => {
      if (err instanceof HttpErrorResponse && err.status === 401 && token) auth.logout();
      return throwError(() => err);
    }),
  );
};
