import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';

import type { WorkflowSummary } from '../api/types';
import { Auth } from '../core/auth';
import { WorkflowsPage } from './workflows-page';

function summary(status: WorkflowSummary['status']): WorkflowSummary {
  return {
    id: 'wf-1',
    status,
    message_preview: 'Charged twice',
    customer_ref: 'CUST-1001',
    channel: 'chat',
    category: status === 'completed' ? 'duplicate_charge' : null,
    urgency: status === 'completed' ? 'normal' : null,
    needs_human_review: null,
    created_at: '2026-10-13T09:00:00Z',
  };
}

function detail(status: WorkflowSummary['status']) {
  return {
    id: 'wf-1',
    status,
    input: { message: 'Charged twice' },
    result: null,
    error: null,
    attempts: 1,
    request_id: null,
    created_at: '2026-10-13T09:00:00Z',
    started_at: null,
    ended_at: null,
    steps: [],
  };
}

describe('WorkflowsPage polling', () => {
  // Regression: polling used to stop after one refresh while a workflow was still queued.
  it('keeps polling while work is pending and stops once it is done', async () => {
    vi.useFakeTimers();
    TestBed.configureTestingModule({
      providers: [provideRouter([]), provideHttpClient(), provideHttpClientTesting()],
    });
    vi.spyOn(TestBed.inject(Auth), 'orgId').mockReturnValue('org-a');
    const backend = TestBed.inject(HttpTestingController);
    TestBed.createComponent(WorkflowsPage);

    // whenStable() would wait for the (fake, frozen) poll timer forever; instead run the
    // effects with TestBed.tick() and let promises settle with a zero-length timer advance.
    const settle = async () => {
      TestBed.tick();
      await vi.advanceTimersByTimeAsync(0);
    };
    const answerList = async (status: WorkflowSummary['status']) => {
      await settle();
      backend.expectOne('/v1/workflows').flush([summary(status)]);
      await settle();
      // The newest request opens in the detail panel.
      for (const req of backend.match('/v1/workflows/wf-1')) req.flush(detail(status));
      await settle();
    };

    await settle();
    backend.expectOne('/v1/ledger/accounts').flush([]); // the customer picker loads once
    await answerList('queued');
    await vi.advanceTimersByTimeAsync(2000);
    await answerList('running'); // still pending: another poll must follow
    await vi.advanceTimersByTimeAsync(2000);
    await answerList('completed');
    await vi.advanceTimersByTimeAsync(10_000);
    backend.expectNone('/v1/workflows'); // done: polling stopped
    backend.verify();
    vi.useRealTimers();
  });
});
