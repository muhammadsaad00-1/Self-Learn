import { inject } from '@angular/core';
import { CanActivateFn, Router } from '@angular/router';

import { Auth } from './auth';

/** Logged-in users only. Loads /me on first navigation (e.g. after a page reload). */
export const authGuard: CanActivateFn = async () => {
  const auth = inject(Auth);
  const router = inject(Router);
  if (!auth.isLoggedIn()) return router.parseUrl('/login');
  if (!auth.me()) {
    try {
      await auth.loadMe();
    } catch {
      return router.parseUrl('/login');
    }
  }
  return true;
};

/**
 * Admins and owners only. This only hides pages: the API enforces roles on every request
 * anyway, so a user who bypasses the guard still gets 403s.
 */
export const adminGuard: CanActivateFn = () =>
  inject(Auth).isAdmin() || inject(Router).parseUrl('/members');
