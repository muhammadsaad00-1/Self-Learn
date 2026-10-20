import { DatePipe, DecimalPipe } from '@angular/common';
import { HttpClient, httpResource } from '@angular/common/http';
import { Component, effect, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { firstValueFrom } from 'rxjs';

import type { Workflow, WorkflowStatus, WorkflowSummary } from '../api/types';
import { Auth } from '../core/auth';
import { errorMessage } from '../core/errors';
import { KnowledgeAnswerView } from '../layout/knowledge-answer';

const PENDING: WorkflowStatus[] = ['queued', 'running'];
const POLL_MS = 2000;

@Component({
  selector: 'app-workflows-page',
  imports: [FormsModule, DatePipe, DecimalPipe, KnowledgeAnswerView],
  template: `
    <h1>Workflows</h1>
    <p class="muted">Paste a customer request. The triage agent classifies it in the background.</p>

    <form class="card composer" (ngSubmit)="submit()">
      <textarea
        [(ngModel)]="message"
        name="message"
        rows="3"
        maxlength="8000"
        placeholder="e.g. I was charged PKR 5,000 twice for transfer TXN-88231 on 3 Oct."
      ></textarea>
      <button type="submit" [disabled]="!message.trim() || submitting()">Submit request</button>
    </form>
    @if (error()) {
      <p class="error" role="alert">{{ error() }}</p>
    }

    <div class="split">
      <table>
        <thead>
          <tr>
            <th>Request</th>
            <th>Status</th>
            <th>Category</th>
            <th>Urgency</th>
            <th>Received</th>
          </tr>
        </thead>
        <tbody>
          @for (w of list.value() ?? []; track w.id) {
            <tr
              class="clickable"
              [class.selected]="w.id === selectedId()"
              (click)="selectedId.set(w.id)"
            >
              <td>{{ w.message_preview }}</td>
              <td>
                <span class="badge" [attr.data-status]="w.status">{{ w.status }}</span>
              </td>
              <td>{{ w.category ?? '—' }}</td>
              <td>
                @if (w.urgency) {
                  <span class="badge" [attr.data-urgency]="w.urgency">{{ w.urgency }}</span>
                }
                @if (w.needs_human_review) {
                  <span class="badge" title="Needs human review">review</span>
                }
              </td>
              <td>{{ w.created_at | date: 'short' }}</td>
            </tr>
          } @empty {
            <tr>
              <td colspan="5" class="muted">No requests yet.</td>
            </tr>
          }
        </tbody>
      </table>

      @if (detail.value(); as wf) {
        <aside class="card detail">
          <h2>Workflow</h2>
          <p class="muted">"{{ wf.input['message'] }}"</p>

          @if (wf.result?.triage; as t) {
            <dl>
              <dt>Category</dt>
              <dd>{{ t.category }}</dd>
              <dt>Urgency</dt>
              <dd>
                <span class="badge" [attr.data-urgency]="t.urgency">{{ t.urgency }}</span>
                @if (t.needs_human_review) {
                  <span class="badge">needs human review</span>
                }
              </dd>
              <dt>Summary</dt>
              <dd>{{ t.summary }}</dd>
              <dt>Details</dt>
              <dd>
                {{ t.details.amount ?? '—' }} {{ t.details.currency ?? '' }} · ref
                {{ t.details.transaction_reference ?? '—' }}
              </dd>
              <dt>Confidence</dt>
              <dd>{{ t.confidence | number: '1.0-2' }}</dd>
              <dt>Reasoning</dt>
              <dd>{{ t.reasoning }}</dd>
            </dl>
          } @else if (wf.status === 'failed') {
            <p class="error">Failed after {{ wf.attempts }} attempts: {{ wf.error }}</p>
          } @else {
            <p class="muted">{{ wf.status }}… (attempt {{ wf.attempts }})</p>
          }

          @if (wf.result?.knowledge; as k) {
            <h3>Policy answer</h3>
            <app-knowledge-answer [answer]="k" />
          }

          <h3>Steps</h3>
          <ol class="steps">
            @for (s of wf.steps; track $index) {
              <li>
                <code>{{ s.node }}</code>
                <span class="badge" [attr.data-status]="s.status">{{ s.status }}</span>
                @if (s.model) {
                  <small class="muted">
                    {{ s.model }} · {{ s.input_tokens }}→{{ s.output_tokens }} tokens ·
                    {{ s.latency_ms }} ms
                  </small>
                }
                @if (s.error) {
                  <small class="error">{{ s.error }}</small>
                }
              </li>
            }
          </ol>
          <small class="muted">Request id: {{ wf.request_id }}</small>
        </aside>
      }
    </div>
  `,
})
export class WorkflowsPage {
  private readonly http = inject(HttpClient);
  private readonly auth = inject(Auth);

  protected message = '';
  protected readonly submitting = signal(false);
  protected readonly error = signal<string | null>(null);
  protected readonly selectedId = signal<string | null>(null);

  // A key per *logical* request: if the POST fails mid-flight and the user clicks again, the
  // same key is sent and the server returns the original workflow instead of a duplicate.
  private idempotencyKey = crypto.randomUUID();

  protected readonly list = httpResource<WorkflowSummary[]>(() => {
    const orgId = this.auth.orgId();
    return orgId ? { url: '/v1/workflows', headers: { 'X-Org-Id': orgId } } : undefined;
  });

  protected readonly detail = httpResource<Workflow>(() => {
    const id = this.selectedId();
    const orgId = this.auth.orgId();
    return id && orgId ? { url: `/v1/workflows/${id}`, headers: { 'X-Org-Id': orgId } } : undefined;
  });

  constructor() {
    // Poll while anything is still queued or running. The effect reads the fetched values
    // themselves (not a derived true/false), so every refresh re-runs it and schedules the
    // next poll; once nothing is pending it schedules nothing and polling stops.
    effect((onCleanup) => {
      const list = this.list.value() ?? [];
      const detail = this.detail.value();
      const pending =
        list.some((w) => PENDING.includes(w.status)) ||
        (detail !== undefined && PENDING.includes(detail.status));
      if (!pending) return;
      const timer = setTimeout(() => {
        this.list.reload();
        if (this.selectedId()) this.detail.reload();
      }, POLL_MS);
      onCleanup(() => clearTimeout(timer));
    });
    // A different org has different workflows: clear the selection.
    effect(() => {
      this.auth.orgId();
      this.selectedId.set(null);
    });
  }

  protected async submit(): Promise<void> {
    this.submitting.set(true);
    this.error.set(null);
    try {
      const wf = await firstValueFrom(
        this.http.post<Workflow>(
          '/v1/workflows',
          { message: this.message.trim() },
          { headers: { 'Idempotency-Key': this.idempotencyKey } },
        ),
      );
      this.message = '';
      this.idempotencyKey = crypto.randomUUID(); // next request is a new logical request
      this.selectedId.set(wf.id);
      this.list.reload();
    } catch (err) {
      this.error.set(errorMessage(err)); // keep the key: a retry is the same request
    } finally {
      this.submitting.set(false);
    }
  }
}
