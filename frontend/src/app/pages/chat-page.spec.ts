import { provideHttpClient, withInterceptors } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';

import type { Chat } from '../api/types';
import { apiInterceptor } from '../core/api.interceptor';
import { Auth } from '../core/auth';
import { ChatPage } from './chat-page';

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

describe('ChatPage', () => {
  let backend: HttpTestingController;
  const settle = async () => {
    TestBed.tick();
    await new Promise((r) => setTimeout(r));
    TestBed.tick();
  };

  beforeEach(() => {
    vi.useFakeTimers({ toFake: ['setInterval'] });
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

  it('uses the link token, sends a message and renders the transfer trace', async () => {
    const fixture = TestBed.createComponent(ChatPage);
    await settle();
    expect(location.hash).toBe(''); // token removed from the address bar
    const first = backend.expectOne('/v1/chat');
    expect(first.request.headers.get('Authorization')).toBe('Bearer customer-token');
    first.flush({ ...ANSWERED, messages: [] });
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
    expect(el.textContent).toContain('Money returned to you');
    expect(el.textContent).toContain('RFD-ABC');
  });
});
