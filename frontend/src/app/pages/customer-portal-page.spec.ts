import { provideHttpClient, withInterceptors } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';

import type { Chat, LedgerTransaction } from '../api/types';
import { apiInterceptor } from '../core/api.interceptor';
import { Auth } from '../core/auth';
import { CustomerSession } from '../core/customer-session';
import { CustomerPortalPage } from './customer-portal-page';

const ANSWERED: Chat = {
  customer_name: 'Usman Tariq',
  mode: 'ai',
  thinking: false,
  messages: [
    {
      id: 'm1',
      role: 'customer',
      body: 'maine 5000 bheje, nahi pohnche',
      card: null,
      created_at: '2026-10-21T09:00:00Z',
    },
    {
      id: 'm2',
      role: 'assistant',
      body: 'Aapke PKR 5,000 wapas aa gaye hain.',
      card: {
        transfer: {
          to: 'IBFT to Ali Raza (HBL)',
          amount: '5000.00',
          currency: 'PKR',
          reference: 'TXN-1',
          network_status: 'failed',
          reversed: true,
        },
        refund: { amount: '5000.00', currency: 'PKR', reference: 'RFD-ABC' },
      },
      created_at: '2026-10-21T09:00:05Z',
    },
  ],
};

const SARA: LedgerTransaction = {
  id: 't1',
  reference: 'TXN-2',
  kind: 'debit',
  amount: '3000.00',
  currency: 'PKR',
  merchant: 'RAAST to Sara Malik',
  status: 'posted',
  transfer_status: 'credited',
  occurred_at: '2026-10-20T09:00:00Z',
};

describe('CustomerPortalPage', () => {
  let backend: HttpTestingController;
  const settle = async () => {
    TestBed.tick();
    await new Promise((r) => setTimeout(r));
    TestBed.tick();
  };
  const customerAuth = (url: string) => backend.expectOne(url).request.headers.get('Authorization');

  beforeEach(() => {
    vi.useFakeTimers({ toFake: ['setInterval'] });
    sessionStorage.clear();
    history.replaceState(null, '', '/chat#customer-token');
    TestBed.configureTestingModule({
      providers: [
        provideRouter([]),
        provideHttpClient(withInterceptors([apiInterceptor])),
        provideHttpClientTesting(),
      ],
    });
    // A staff member is logged in in the same browser: their token must never be sent.
    vi.spyOn(TestBed.inject(Auth), 'token').mockReturnValue('staff-token');
    backend = TestBed.inject(HttpTestingController);
  });

  afterEach(() => vi.useRealTimers());

  it('takes the link token, chats, and shows activity with transfer status', async () => {
    const fixture = TestBed.createComponent(CustomerPortalPage);
    await settle();
    expect(location.hash).toBe(''); // token removed from the address bar
    expect(TestBed.inject(CustomerSession).loggedIn()).toBe(true);

    const first = backend.expectOne('/v1/chat');
    expect(first.request.headers.get('Authorization')).toBe('Bearer customer-token');
    first.flush({ ...ANSWERED, messages: [] });
    backend.expectOne('/v1/customer/me').flush({
      customer_ref: 'CUST-1004',
      customer_name: 'Usman Tariq',
      phone: '+92 *** ***4504',
      card_status: 'active',
    });
    backend.expectOne('/v1/customer/transactions').flush([]);
    backend.expectOne('/v1/customer/cases').flush([]);
    await settle();

    const el = fixture.nativeElement as HTMLElement;
    expect(el.textContent).toContain('Hi Usman');
    [...el.querySelectorAll<HTMLButtonElement>('button.chip')][1].click();
    await settle();
    const sent = backend.expectOne('/v1/chat/messages');
    expect(sent.request.body.text).toContain('5000');
    expect(sent.request.headers.get('Authorization')).toBe('Bearer customer-token');
    sent.flush({});
    await settle();
    backend.expectOne('/v1/chat').flush(ANSWERED);
    await settle();
    expect(el.textContent).toContain('Rejected by the recipient');
    expect(el.textContent).toContain('RFD-ABC');

    // The answer changed the account: the rest of the app reloads.
    expect(customerAuth('/v1/customer/me')).toBe('Bearer customer-token');
    backend.expectOne('/v1/customer/transactions').flush([SARA]);
    backend.expectOne('/v1/customer/cases').flush([]);
    [...el.querySelectorAll('.tabbar button')]
      .find((b) => b.textContent?.includes('Activity'))!
      .dispatchEvent(new Event('click'));
    await settle();
    expect(el.textContent).toContain('RAAST to Sara Malik');
    expect(el.textContent).toContain('Delivered');
  });
});
