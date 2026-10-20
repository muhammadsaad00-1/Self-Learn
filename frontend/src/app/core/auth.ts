import { HttpClient, HttpParams } from '@angular/common/http';
import { computed, inject, Injectable, signal } from '@angular/core';
import { Router } from '@angular/router';
import { firstValueFrom } from 'rxjs';

import type {
  Membership,
  MeResponse,
  SignupRequest,
  SignupResponse,
  TokenResponse,
} from '../api/types';

const STORAGE_KEY = 'agentflow.session';

interface Session {
  token: string;
  expiresAt: number; // epoch ms
  orgId: string | null; // organization selected in the org switcher
}

/**
 * Login state for the whole app, as signals.
 *
 * The token lives in sessionStorage: it survives a page reload but not closing the tab.
 * Any script running on the page could read it (an XSS risk), which is acceptable for now
 * because tokens expire after 30 minutes. An httpOnly refresh cookie is the upgrade path (ADR 0007).
 */
@Injectable({ providedIn: 'root' })
export class Auth {
  private readonly http = inject(HttpClient);
  private readonly router = inject(Router);

  private readonly session = signal<Session | null>(loadSession());
  readonly me = signal<MeResponse | null>(null);

  readonly token = computed(() => this.session()?.token ?? null);
  readonly orgId = computed(() => this.session()?.orgId ?? null);
  readonly isLoggedIn = computed(() => this.token() !== null);
  readonly membership = computed(
    () => this.me()?.memberships.find((m) => m.org_id === this.orgId()) ?? null,
  );
  readonly role = computed(() => this.membership()?.role ?? null);
  readonly isAdmin = computed(() => this.role() === 'owner' || this.role() === 'admin');

  async login(email: string, password: string): Promise<void> {
    // OAuth2 password flow: form-encoded `username` + `password`, not JSON.
    const form = new HttpParams().set('username', email).set('password', password);
    await this.start(await firstValueFrom(this.http.post<TokenResponse>('/v1/auth/login', form)));
  }

  async signup(request: SignupRequest): Promise<void> {
    const res = await firstValueFrom(this.http.post<SignupResponse>('/v1/auth/signup', request));
    await this.start(res, res.org_id);
  }

  /** Reload the user and their memberships; keep the selected org if still a member. */
  async loadMe(): Promise<void> {
    const me = await firstValueFrom(this.http.get<MeResponse>('/v1/me'));
    this.me.set(me);
    if (!me.memberships.some((m) => m.org_id === this.orgId())) {
      this.selectOrg(me.memberships[0]?.org_id ?? null);
    }
  }

  selectOrg(orgId: string | null): void {
    const current = this.session();
    if (current) this.save({ ...current, orgId });
  }

  /**
   * Switch to another of the user's companies (like switching Chrome profiles). Same token;
   * from now on every API call carries the new X-Org-Id, and pages reload their data.
   */
  switchCompany(orgId: string): void {
    this.selectOrg(orgId);
    // The audit log is admin-only: leave it if the role in the new company doesn't allow it.
    if (this.router.url.startsWith('/audit') && !this.isAdmin()) {
      void this.router.navigateByUrl('/workflows');
    }
  }

  /** Create another company owned by this user, then switch to it. */
  async addCompany(orgName: string, password: string): Promise<Membership> {
    const created = await firstValueFrom(
      this.http.post<Membership>('/v1/orgs', { org_name: orgName, password }),
    );
    await this.loadMe(); // the new company now appears in the menu
    this.switchCompany(created.org_id);
    return created;
  }

  logout(): void {
    this.save(null);
    this.me.set(null);
    void this.router.navigateByUrl('/login');
  }

  private async start(res: TokenResponse, orgId: string | null = null): Promise<void> {
    this.save({ token: res.access_token, expiresAt: Date.now() + res.expires_in * 1000, orgId });
    await this.loadMe();
  }

  private save(session: Session | null): void {
    this.session.set(session);
    if (session) sessionStorage.setItem(STORAGE_KEY, JSON.stringify(session));
    else sessionStorage.removeItem(STORAGE_KEY);
  }
}

function loadSession(): Session | null {
  try {
    const session = JSON.parse(sessionStorage.getItem(STORAGE_KEY) ?? 'null') as Session | null;
    return session && session.expiresAt > Date.now() ? session : null;
  } catch {
    return null;
  }
}
