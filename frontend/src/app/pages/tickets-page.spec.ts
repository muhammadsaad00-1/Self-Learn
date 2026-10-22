import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';

import type { Ticket } from '../api/types';
import { Auth } from '../core/auth';
import { TicketsPage } from './tickets-page';

const TICKET: Ticket = {
  id: 't-1',
  code: 'TCK-1042',
  workflow_id: 'wf-1',
  title: 'unauthorized_transaction: Customer reports stolen funds',
  description: 'Why a human is needed:\n- Suspected fraud',
  priority: 'critical',
  category: 'unauthorized_transaction',
  customer_ref: 'CUST-1003',
  status: 'open',
  assignee_id: null,
  resolution_note: null,
  created_at: '2026-10-21T09:00:00Z',
  resolved_at: null,
  activity: [],
};

const MEMBERS = [
  { user_id: 'u-me', email: 'me@x.io', full_name: 'Ayesha', role: 'owner', joined_at: '' },
  { user_id: 'u-sana', email: 'sana@x.io', full_name: 'Sana', role: 'member', joined_at: '' },
];

describe('TicketsPage', () => {
  let backend: HttpTestingController;
  const settle = async () => {
    TestBed.tick();
    await new Promise((r) => setTimeout(r));
    TestBed.tick();
  };

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [provideRouter([]), provideHttpClient(), provideHttpClientTesting()],
    });
    vi.spyOn(TestBed.inject(Auth), 'orgId').mockReturnValue('org-a');
    backend = TestBed.inject(HttpTestingController);
  });

  it('lists open tickets and takes one', async () => {
    const fixture = TestBed.createComponent(TicketsPage);
    await settle();
    const list = backend.expectOne((r) => r.url === '/v1/tickets');
    expect(list.request.params.get('status')).toBe('open');
    list.flush([TICKET]);
    backend.expectOne('/v1/members').flush(MEMBERS);
    await settle();
    const el = fixture.nativeElement as HTMLElement;
    expect(el.textContent).toContain('TCK-1042');
    expect(el.textContent).toContain('Suspected fraud');

    [...el.querySelectorAll('button')].find((b) => b.textContent?.includes('Take it'))!.click();
    await settle();
    const patch = backend.expectOne('/v1/tickets/t-1');
    expect(patch.request.method).toBe('PATCH');
    expect(patch.request.body).toEqual({
      action: 'take',
      note: null,
      assignee_id: null,
      customer_message: null,
    });
    patch.flush({ ...TICKET, status: 'in_progress' });
    await settle();
    backend.expectOne((r) => r.url === '/v1/tickets').flush([{ ...TICKET, status: 'in_progress' }]);
  });

  it('hands a case to a teammate with a note and shows the trail', async () => {
    const fixture = TestBed.createComponent(TicketsPage);
    await settle();
    backend.expectOne((r) => r.url === '/v1/tickets').flush([TICKET]);
    backend.expectOne('/v1/members').flush(MEMBERS);
    await settle();
    const el = fixture.nativeElement as HTMLElement;

    const note = el.querySelector<HTMLInputElement>('input[placeholder^="Note"]')!;
    note.value = 'Fraud desk, yours';
    note.dispatchEvent(new Event('input'));
    const to = el.querySelector<HTMLSelectElement>('select[aria-label="Hand off to"]')!;
    to.value = to.options[2].value; // Sana
    to.dispatchEvent(new Event('change'));
    await settle();
    [...el.querySelectorAll('button')].find((b) => b.textContent?.includes('Hand off'))!.click();
    await settle();

    const patch = backend.expectOne('/v1/tickets/t-1');
    expect(patch.request.body).toMatchObject({
      action: 'assign',
      assignee_id: 'u-sana',
      note: 'Fraud desk, yours',
    });
    const handedOff = {
      ...TICKET,
      status: 'in_progress' as const,
      assignee_id: 'u-sana',
      activity: [
        {
          at: '2026-10-22T09:00:00Z',
          by: 'Ayesha',
          kind: 'assign',
          to: 'Sana',
          text: 'Fraud desk, yours',
        },
      ],
    };
    patch.flush(handedOff);
    await settle();
    backend.expectOne((r) => r.url === '/v1/tickets').flush([handedOff]);
    await settle();
    expect(el.textContent).toContain('with Sana');
    expect(el.textContent).toContain('handed it to Sana');
  });
});
