import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { provideRouter, Router } from '@angular/router';

import { CustomerSession } from '../core/customer-session';
import { CustomerLoginPage } from './customer-login-page';

describe('CustomerLoginPage', () => {
  const settle = async () => {
    TestBed.tick();
    await new Promise((r) => setTimeout(r));
    TestBed.tick();
  };

  it('signs in with phone + username, choosing the bank when there are several', async () => {
    sessionStorage.clear();
    TestBed.configureTestingModule({
      providers: [provideRouter([]), provideHttpClient(), provideHttpClientTesting()],
    });
    const backend = TestBed.inject(HttpTestingController);
    vi.spyOn(TestBed.inject(Router), 'navigateByUrl').mockResolvedValue(true);
    const fixture = TestBed.createComponent(CustomerLoginPage);
    await settle();
    const page = fixture.componentInstance as unknown as {
      phone: string;
      username: string;
      code: string;
      submit(): Promise<void>;
    };
    const el = fixture.nativeElement as HTMLElement;

    Object.assign(page, { phone: '0300 1234504', username: 'usman.tariq' });
    const started = page.submit();
    const start = backend.expectOne('/v1/customer/login/start');
    expect(start.request.body).toEqual({
      phone: '0300 1234504',
      username: 'usman.tariq',
      bank_id: null,
    });
    start.flush({
      sent: false,
      bank: null,
      banks: [
        { id: 'b1', name: 'Mehran Microfinance' },
        { id: 'b2', name: 'Second Bank' },
      ],
      dev_code: null,
    });
    await started;
    await settle();
    expect(el.textContent).toContain('Which bank?');

    [...el.querySelectorAll<HTMLButtonElement>('button.choice')][1].click();
    await settle();
    const chosen = backend.expectOne('/v1/customer/login/start');
    expect(chosen.request.body.bank_id).toBe('b2');
    chosen.flush({
      sent: true,
      bank: { id: 'b2', name: 'Second Bank' },
      banks: [],
      dev_code: '123456',
    });
    await settle();
    expect(el.textContent).toContain('123456');

    page.code = '123456';
    const verified = page.submit();
    const verify = backend.expectOne('/v1/customer/login/verify');
    expect(verify.request.body).toEqual({ bank_id: 'b2', phone: '0300 1234504', code: '123456' });
    verify.flush({ customer_token: 'tok' });
    await verified;
    const session = TestBed.inject(CustomerSession);
    expect(session.loggedIn()).toBe(true);
    expect(session.bankName()).toBe('Second Bank');
  });
});
