import { HttpClient, httpResource } from '@angular/common/http';
import { Component, computed, effect, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { firstValueFrom } from 'rxjs';

import type { LedgerAccount, Workflow, WorkflowStatus, WorkflowSummary } from '../api/types';
import { Auth } from '../core/auth';
import { errorMessage } from '../core/errors';
import { FORMAT } from '../core/format';
import { DecisionTrail } from '../layout/decision-trail';
import { Icon } from '../layout/icon';

const PENDING: WorkflowStatus[] = ['queued', 'running'];
const POLL_MS = 2000;

/** Every customer request (from the customer app, email or entered by staff) and what the
 *  agents did with it, as a queue with a detail timeline. */
@Component({
  selector: 'app-workflows-page',
  imports: [FormsModule, DecisionTrail, Icon, ...FORMAT],
  template: `
    <div class="page wide">
      <header class="page-header">
        <div>
          <h1>Requests</h1>
          <p class="subtitle">Every customer request, and exactly what the agents did with it.</p>
        </div>
        <div class="actions">
          @if (auth.isAdmin() && accounts.value()?.length === 0) {
            <button type="button" class="secondary" (click)="loadDemoData()">
              <app-icon name="database" /> Load demo customers
            </button>
          }
          <button type="button" (click)="composing.set(!composing())">
            <app-icon [name]="composing() ? 'x' : 'plus'" />
            {{ composing() ? 'Close' : 'New request' }}
          </button>
        </div>
      </header>

      @if (composing()) {
        <form class="card pad stack" style="margin-bottom: 16px" (ngSubmit)="submit()">
          <div>
            <h2>Log a request</h2>
            <p class="muted">
              For requests that arrive by phone or email. The agents take it from here.
            </p>
          </div>
          <textarea
            [(ngModel)]="message"
            name="message"
            rows="3"
            maxlength="8000"
            placeholder="e.g. I was charged PKR 5,000 twice for my electricity bill yesterday."
          ></textarea>
          <div class="row">
            <select [(ngModel)]="customerRef" name="customer" aria-label="Customer">
              <option value="">Unknown customer</option>
              @for (c of accounts.value() ?? []; track c.customer_ref) {
                <option [value]="c.customer_ref">
                  {{ c.customer_name }} · {{ c.customer_ref }}
                </option>
              }
            </select>
            <span class="spacer"></span>
            <button type="submit" [disabled]="!message.trim() || submitting()">
              <app-icon name="send" /> Submit request
            </button>
          </div>
        </form>
      }
      @if (error()) {
        <div class="callout" data-tone="danger" role="alert" style="margin-bottom: 16px">
          {{ error() }}
        </div>
      }

      <div class="split">
        <section class="card queue">
          @for (w of list.value() ?? []; track w.id) {
            <button
              type="button"
              class="queue-item"
              [class.selected]="w.id === activeId()"
              (click)="selectedId.set(w.id)"
            >
              <span class="prio" [attr.data-p]="w.urgency"></span>
              <span class="stack-sm" style="gap: 4px; min-width: 0">
                <span class="line">
                  <span class="title truncate">{{ w.message_preview }}</span>
                  <span class="time">{{ w.created_at | ago }}</span>
                </span>
                <span class="line">
                  @if (w.status === 'completed') {
                    @if (w.category) {
                      <span class="badge">{{ w.category | human }}</span>
                    }
                  } @else {
                    <span class="badge dot" [attr.data-status]="w.status">{{
                      w.status | human
                    }}</span>
                  }
                  <small class="muted truncate">
                    {{ w.customer_ref ?? 'Unknown customer' }} ·
                    {{ w.channel === 'chat' ? 'customer app' : w.channel }}
                  </small>
                </span>
              </span>
            </button>
          } @empty {
            <div class="empty">
              <span class="empty-icon"><app-icon name="inbox" [size]="20" /></span>
              <strong>No requests yet</strong>
              <span>They appear here as customers write in.</span>
            </div>
          }
        </section>

        <section class="card detail-sticky">
          @if (detail.value(); as wf) {
            <div class="card-head">
              <div class="grow stack-sm">
                <div class="row">
                  <span class="badge dot" [attr.data-status]="wf.status">{{
                    wf.status | human
                  }}</span>
                  <small class="muted">
                    {{ wf.input['customer_ref'] ?? 'Unknown customer' }} ·
                    {{ wf.input['channel'] === 'chat' ? 'customer app' : wf.input['channel'] }} ·
                    {{ wf.created_at | ago }}
                  </small>
                </div>
                <p class="quote" style="font-size: 15px">{{ wf.input['message'] }}</p>
              </div>
            </div>
            <div class="card-body stack">
              @if (wf.status === 'failed') {
                <div class="callout" data-tone="danger">
                  The agents gave up after {{ wf.attempts }} attempts: {{ wf.error }}
                </div>
              } @else if (!wf.result) {
                <div class="stack-sm">
                  <div class="skeleton" style="width: 60%"></div>
                  <div class="skeleton" style="width: 80%"></div>
                  <small class="muted">The agents are working on it…</small>
                </div>
              }
              @if (wf.result; as r) {
                <app-decision-trail [result]="r" />
              }
              <details>
                <summary class="muted" style="cursor: pointer">
                  Technical details · {{ wf.steps.length }} steps
                </summary>
                <div class="table-wrap" style="margin-top: 8px">
                  <table>
                    <thead>
                      <tr>
                        <th>Step</th>
                        <th>Model</th>
                        <th>Tokens</th>
                        <th>Time</th>
                      </tr>
                    </thead>
                    <tbody>
                      @for (s of wf.steps; track $index) {
                        <tr>
                          <td>
                            <code>{{ s.node }}</code>
                            @if (s.status === 'failed') {
                              <span class="badge" data-tone="danger">failed</span>
                              <div>
                                <small class="error">{{ s.error }}</small>
                              </div>
                            }
                          </td>
                          <td>
                            <small class="muted">{{ s.model ?? '—' }}</small>
                          </td>
                          <td>
                            <small class="muted">{{
                              s.model ? s.input_tokens + ' → ' + s.output_tokens : '—'
                            }}</small>
                          </td>
                          <td>
                            <small class="muted">{{
                              s.latency_ms !== null ? s.latency_ms + ' ms' : '—'
                            }}</small>
                          </td>
                        </tr>
                      }
                    </tbody>
                  </table>
                </div>
                <small class="faint">Request id {{ wf.request_id }}</small>
              </details>
            </div>
          } @else {
            <div class="empty">
              <span class="empty-icon"><app-icon name="activity" [size]="20" /></span>
              <strong>Select a request</strong>
              <span>See what was understood, checked, decided and done.</span>
            </div>
          }
        </section>
      </div>
    </div>
  `,
})
export class WorkflowsPage {
  private readonly http = inject(HttpClient);
  protected readonly auth = inject(Auth);

  protected message = '';
  protected customerRef = '';
  protected readonly submitting = signal(false);
  protected readonly error = signal<string | null>(null);
  protected readonly selectedId = signal<string | null>(null);
  protected readonly composing = signal(false);
  /** The chosen request, else the newest: the page never opens on an empty panel. A computed
   *  (compared by value), so list refreshes don't refetch the same detail. */
  protected readonly activeId = computed(() => this.selectedId() ?? this.list.value()?.[0]?.id);

  // A key per *logical* request: if the POST fails mid-flight and the user clicks again, the
  // same key is sent and the server returns the original workflow instead of a duplicate.
  private idempotencyKey = crypto.randomUUID();

  protected readonly list = httpResource<WorkflowSummary[]>(() => {
    const orgId = this.auth.orgId();
    return orgId ? { url: '/v1/workflows', headers: { 'X-Org-Id': orgId } } : undefined;
  });

  protected readonly accounts = httpResource<LedgerAccount[]>(() => {
    const orgId = this.auth.orgId();
    return orgId ? { url: '/v1/ledger/accounts', headers: { 'X-Org-Id': orgId } } : undefined;
  });

  protected async loadDemoData(): Promise<void> {
    try {
      await firstValueFrom(this.http.post('/v1/ledger/demo-data', {}));
    } catch (err) {
      this.error.set(errorMessage(err));
    }
    this.accounts.reload();
  }

  protected readonly detail = httpResource<Workflow>(() => {
    const id = this.activeId();
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
        if (this.activeId()) this.detail.reload();
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
          { message: this.message.trim(), customer_ref: this.customerRef || null },
          { headers: { 'Idempotency-Key': this.idempotencyKey } },
        ),
      );
      this.message = '';
      this.composing.set(false);
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
