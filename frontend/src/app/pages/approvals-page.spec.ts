import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';

import type { Approval } from '../api/types';
import { Auth } from '../core/auth';
import { ApprovalsPage } from './approvals-page';

const PENDING: Approval = {
  id: 'ap-1',
  workflow_id: 'wf-1',
  status: 'pending',
  action: { action: 'refund_transaction', transaction_reference: 'TXN-2', amount: '5000.00' },
  policy_reasons: ["Refunds above 2000 need a second officer's approval"],
  requested_by: 'u-maker',
  requested_at: '2026-10-21T09:00:00Z',
  expires_at: '2026-10-22T09:00:00Z',
  decided_by: null,
  decided_at: null,
  note: null,
  message_preview: 'My bill was deducted twice',
  customer_ref: 'CUST-1001',
  category: 'duplicate_charge',
  urgency: 'high',
};

describe('ApprovalsPage', () => {
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

  it('lists pending approvals with the policy reasons', async () => {
    const fixture = TestBed.createComponent(ApprovalsPage);
    await settle();
    const req = backend.expectOne((r) => r.url === '/v1/approvals');
    expect(req.request.params.get('status')).toBe('pending');
    req.flush([PENDING]);
    await settle();
    const text = (fixture.nativeElement as HTMLElement).textContent ?? '';
    expect(text).toContain('Refund 5000.00');
    expect(text).toContain("need a second officer's approval");
  });

  it('shows the maker-checker refusal from the server', async () => {
    const fixture = TestBed.createComponent(ApprovalsPage);
    await settle();
    backend.expectOne((r) => r.url === '/v1/approvals').flush([PENDING]);
    await settle();
    const approve = [...(fixture.nativeElement as HTMLElement).querySelectorAll('button')].find(
      (b) => b.textContent?.trim() === 'Approve',
    )!;
    approve.click();
    await settle();
    const post = backend.expectOne('/v1/approvals/ap-1/approve');
    expect(post.request.body).toEqual({ note: null });
    post.flush(
      { detail: 'Maker-checker: you submitted this request, so another officer must decide it' },
      { status: 403, statusText: 'Forbidden' },
    );
    await settle();
    backend.expectOne((r) => r.url === '/v1/approvals').flush([PENDING]); // list refreshed
    await settle();
    expect((fixture.nativeElement as HTMLElement).textContent).toContain('Maker-checker');
  });
});
