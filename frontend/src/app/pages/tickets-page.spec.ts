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
};

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
    await settle();
    const el = fixture.nativeElement as HTMLElement;
    expect(el.textContent).toContain('TCK-1042');
    expect(el.textContent).toContain('Suspected fraud');

    [...el.querySelectorAll('button')].find((b) => b.textContent?.includes('Take it'))!.click();
    await settle();
    const patch = backend.expectOne('/v1/tickets/t-1');
    expect(patch.request.method).toBe('PATCH');
    expect(patch.request.body).toEqual({ action: 'take', note: null });
    patch.flush({ ...TICKET, status: 'in_progress' });
    await settle();
    backend.expectOne((r) => r.url === '/v1/tickets').flush([{ ...TICKET, status: 'in_progress' }]);
  });
});
