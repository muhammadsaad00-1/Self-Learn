import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';

import type { KnowledgeAnswer, KnowledgeDocument } from '../api/types';
import { Auth } from '../core/auth';
import { KnowledgePage } from './knowledge-page';

function doc(status: KnowledgeDocument['status']): KnowledgeDocument {
  return {
    id: 'd1',
    title: 'Refund policy',
    filename: 'refunds.md',
    content_type: 'text/markdown',
    size_bytes: 2048,
    status,
    error: null,
    chunk_count: status === 'ready' ? 4 : 0,
    quarantined_chunks: 0,
    created_at: '2026-10-20T09:00:00Z',
    updated_at: '2026-10-20T09:00:00Z',
  };
}

describe('KnowledgePage', () => {
  let backend: HttpTestingController;

  // Run effects, let the HTTP response land (a microtask), then run the effects it triggers.
  const settle = async () => {
    TestBed.tick();
    await vi.advanceTimersByTimeAsync(0);
    TestBed.tick();
  };

  beforeEach(() => {
    vi.useFakeTimers();
    TestBed.configureTestingModule({
      providers: [provideRouter([]), provideHttpClient(), provideHttpClientTesting()],
    });
    const auth = TestBed.inject(Auth);
    vi.spyOn(auth, 'orgId').mockReturnValue('org-a');
    vi.spyOn(auth, 'isAdmin').mockReturnValue(true);
    backend = TestBed.inject(HttpTestingController);
  });

  afterEach(() => vi.useRealTimers());

  it('polls while a document is being ingested and stops when it is ready', async () => {
    TestBed.createComponent(KnowledgePage);
    await settle();
    backend.expectOne('/v1/knowledge/documents').flush([doc('processing')]);
    await settle();
    await vi.advanceTimersByTimeAsync(2000); // poll timer fires -> reload()
    await settle(); // the reloaded request is sent on the next tick
    backend.expectOne('/v1/knowledge/documents').flush([doc('ready')]);
    await settle();
    await vi.advanceTimersByTimeAsync(10_000);
    await settle();
    backend.expectNone('/v1/knowledge/documents'); // ready: polling stopped
  });

  it('uploads the chosen file as multipart form data', async () => {
    const fixture = TestBed.createComponent(KnowledgePage);
    await settle();
    backend.expectOne('/v1/knowledge/documents').flush([]);
    const page = fixture.componentInstance as unknown as {
      file: File | null;
      title: string;
      upload(): Promise<void>;
    };
    page.file = new File(['# Refunds'], 'refunds.md', { type: 'text/markdown' });
    page.title = 'Refund policy';
    const done = page.upload();
    const req = backend.expectOne({ method: 'POST', url: '/v1/knowledge/documents' });
    const body = req.request.body as FormData;
    expect((body.get('file') as File).name).toBe('refunds.md');
    expect(body.get('title')).toBe('Refund policy');
    req.flush(doc('pending'));
    await done;
    expect(page.file).toBeNull();
  });

  it('shows a verified answer with its citations', async () => {
    const fixture = TestBed.createComponent(KnowledgePage);
    await settle();
    backend.expectOne('/v1/knowledge/documents').flush([]);
    const page = fixture.componentInstance as unknown as { question: string; ask(): Promise<void> };
    page.question = 'How long does a reversal take?';
    const done = page.ask();
    const answer: KnowledgeAnswer = {
      status: 'answered',
      answer: 'Within 3 business days.',
      citations: [
        {
          number: 1,
          chunk_id: 'c1',
          document_id: 'd1',
          document_title: 'Refund policy',
          section: 'Duplicate charges',
          excerpt: 'Duplicate debits are reversed',
        },
      ],
      queries: ['duplicate debit reversal'],
      reason: null,
    };
    backend.expectOne('/v1/knowledge/ask').flush(answer);
    await done;
    await settle();
    const text = (fixture.nativeElement as HTMLElement).textContent ?? '';
    expect(text).toContain('Within 3 business days.');
    expect(text).toContain('[1] Refund policy');
    expect(text).toContain('Verified answer');
  });
});
