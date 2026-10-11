import { HttpClient, provideHttpClient, withInterceptors } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { provideRouter, Routes } from '@angular/router';
import { firstValueFrom } from 'rxjs';

import type { MeResponse } from '../api/types';
import { apiInterceptor } from './api.interceptor';
import { Auth } from './auth';

// Logout navigates to /login, so the test router needs that route to exist.
const ROUTES: Routes = [{ path: 'login', children: [] }];

const ME: MeResponse = {
  user: { id: 'u1', email: 'sara@easypay.example', full_name: 'Sara' },
  memberships: [
    { org_id: 'org-a', org_name: 'EasyPay', org_slug: 'easypay', role: 'owner' },
    { org_id: 'org-b', org_name: 'MicroFin', org_slug: 'microfin', role: 'member' },
  ],
};

describe('Auth + apiInterceptor', () => {
  let auth: Auth;
  let http: HttpClient;
  let backend: HttpTestingController;

  beforeEach(() => {
    sessionStorage.clear();
    TestBed.configureTestingModule({
      providers: [
        provideRouter(ROUTES),
        provideHttpClient(withInterceptors([apiInterceptor])),
        provideHttpClientTesting(), // requests go to `backend` instead of the network
      ],
    });
    auth = TestBed.inject(Auth);
    http = TestBed.inject(HttpClient);
    backend = TestBed.inject(HttpTestingController);
  });

  afterEach(() => backend.verify()); // fails if a request was left unanswered

  async function logIn(): Promise<void> {
    const done = auth.login('sara@easypay.example', 'correct horse battery staple');
    backend
      .expectOne('/v1/auth/login')
      .flush({ access_token: 'tok', token_type: 'bearer', expires_in: 1800 });
    await Promise.resolve(); // let login() continue to the /me call
    backend.expectOne('/v1/me').flush(ME);
    await done;
  }

  it('logs in with an OAuth2 form, then selects the first org', async () => {
    const done = auth.login('sara@easypay.example', 'pw');
    const req = backend.expectOne('/v1/auth/login');
    const form = new URLSearchParams(req.request.body.toString()); // decode the form body
    expect(Object.fromEntries(form)).toEqual({ username: 'sara@easypay.example', password: 'pw' });
    req.flush({ access_token: 'tok', token_type: 'bearer', expires_in: 1800 });
    await Promise.resolve();
    backend.expectOne('/v1/me').flush(ME);
    await done;

    expect(auth.isLoggedIn()).toBe(true);
    expect(auth.orgId()).toBe('org-a');
    expect(auth.role()).toBe('owner');
    expect(auth.isAdmin()).toBe(true);
  });

  it('sends the token and the selected org on API calls only', async () => {
    await logIn();
    auth.selectOrg('org-b');

    void firstValueFrom(http.get('/v1/members'));
    const api = backend.expectOne('/v1/members');
    expect(api.request.headers.get('Authorization')).toBe('Bearer tok');
    expect(api.request.headers.get('X-Org-Id')).toBe('org-b');
    api.flush([]);

    void firstValueFrom(http.get('/assets/logo.svg'));
    const other = backend.expectOne('/assets/logo.svg');
    expect(other.request.headers.has('Authorization')).toBe(false); // never leak the token
    other.flush('');
  });

  it('role follows the selected org', async () => {
    await logIn();
    auth.selectOrg('org-b');
    expect(auth.role()).toBe('member');
    expect(auth.isAdmin()).toBe(false);
  });

  it('logs out when the API answers 401', async () => {
    await logIn();
    const call = firstValueFrom(http.get('/v1/members'));
    backend
      .expectOne('/v1/members')
      .flush({ detail: 'Invalid or expired token' }, { status: 401, statusText: 'Unauthorized' });
    await expect(call).rejects.toBeTruthy();
    expect(auth.isLoggedIn()).toBe(false);
    expect(sessionStorage.getItem('agentflow.session')).toBeNull();
  });

  it('ignores an expired stored session', () => {
    sessionStorage.setItem(
      'agentflow.session',
      JSON.stringify({ token: 'old', expiresAt: Date.now() - 1000, orgId: 'org-a' }),
    );
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      providers: [provideRouter(ROUTES), provideHttpClient(), provideHttpClientTesting()],
    });
    expect(TestBed.inject(Auth).isLoggedIn()).toBe(false);
  });
});
