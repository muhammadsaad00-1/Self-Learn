import { provideHttpClient } from '@angular/common/http';
import { provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';

import { Auth } from '../core/auth';
import { SignupWizard } from './signup-wizard';

describe('SignupWizard', () => {
  it('validates each step and sends the business profile', async () => {
    TestBed.configureTestingModule({
      providers: [provideRouter([]), provideHttpClient(), provideHttpClientTesting()],
    });
    const signup = vi.spyOn(TestBed.inject(Auth), 'signup').mockResolvedValue();
    const fixture = TestBed.createComponent(SignupWizard);
    const w = fixture.componentInstance as unknown as Record<string, unknown> & {
      next(): Promise<void>;
      step(): number;
      error(): string | null;
    };

    await w.next();
    expect(w.step()).toBe(0); // nothing filled in: stays
    expect(w.error()).toContain('valid work email');
    Object.assign(w, {
      fullName: 'Sana Ali',
      email: 'sana@bank.pk',
      password: 'a long passphrase',
    });
    await w.next();
    expect(w.step()).toBe(1);

    Object.assign(w, { orgName: 'Mehran Microfinance' });
    await w.next();
    expect(w.error()).toContain('what kind of business'); // industry is required
    Object.assign(w, { industry: 'microfinance_bank' });
    await w.next();
    expect(w.step()).toBe(2);

    Object.assign(w, { monthlyQueries: 20000 });
    await w.next();
    expect(signup).toHaveBeenCalledWith(
      expect.objectContaining({
        org_name: 'Mehran Microfinance',
        profile: expect.objectContaining({
          industry: 'microfinance_bank',
          monthly_queries: 20000,
          customers: '100k_1m',
          channels: ['app_chat', 'email'],
        }),
      }),
    );
  });
});
