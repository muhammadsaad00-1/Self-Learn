import { DecimalPipe } from '@angular/common';
import { HttpClient, httpResource } from '@angular/common/http';
import { Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { firstValueFrom } from 'rxjs';

import type { Approval } from '../api/types';
import { Auth } from '../core/auth';
import { errorMessage } from '../core/errors';
import { FORMAT } from '../core/format';
import { Icon } from '../layout/icon';

/** Maker-checker inbox: actions the policy engine won't run without a second officer. */
@Component({
  selector: 'app-approvals-page',
  imports: [DecimalPipe, FormsModule, Icon, ...FORMAT],
  template: `
    <div class="page">
      <header class="page-header">
        <div>
          <h1>Approvals</h1>
          <p class="subtitle">
            Maker-checker: refunds above the automatic limit need a second officer. You can't
            approve a request you submitted yourself.
          </p>
        </div>
        <div class="actions">
          <div class="segmented">
            <button type="button" [class.active]="!showAll" (click)="showAll = false; reload()">
              Pending
            </button>
            <button type="button" [class.active]="showAll" (click)="showAll = true; reload()">
              All
            </button>
          </div>
        </div>
      </header>
      @if (error()) {
        <div class="callout" data-tone="danger" role="alert" style="margin-bottom: 12px">
          {{ error() }}
        </div>
      }

      <div class="stack">
        @for (a of approvals.value() ?? []; track a.id) {
          <section class="card">
            <div class="card-head">
              <span class="avatar lg square"><app-icon name="money" [size]="18" /></span>
              <div class="grow stack-sm" style="gap: 2px">
                <h2>Refund PKR {{ amountOf(a) | number: '1.0-0' }}</h2>
                <small class="muted">
                  {{ a.customer_ref }} · {{ a.category | human }} · requested
                  {{ a.requested_at | ago }}
                  @if (a.status === 'pending') {
                    · expires {{ a.expires_at | ago }}
                  }
                </small>
              </div>
              <span class="badge dot" [attr.data-tone]="a.status | tone">{{
                a.status | human
              }}</span>
            </div>
            <div class="card-body stack">
              <p class="quote">{{ a.message_preview }}</p>
              <div class="stack-sm">
                <h3 class="section-title">Why it needs you</h3>
                <ul class="reasons">
                  @for (r of a.policy_reasons; track r) {
                    <li>{{ r }}</li>
                  }
                </ul>
              </div>
              <div class="stack-sm">
                <h3 class="section-title">What the agents found</h3>
                <span class="muted">{{ a.action['rationale'] }}</span>
                <small class="faint"
                  >Transaction
                  <span class="mono">{{ a.action['transaction_reference'] }}</span></small
                >
              </div>
              @if (a.status === 'pending') {
                <div class="row" style="border-top: 1px solid var(--border); padding-top: 14px">
                  <input
                    class="grow"
                    [(ngModel)]="notes[a.id]"
                    [name]="'note-' + a.id"
                    placeholder="Note (optional)"
                    maxlength="500"
                  />
                  <button class="danger" (click)="decide(a, false)" [disabled]="busy()">
                    Reject
                  </button>
                  <button (click)="decide(a, true)" [disabled]="busy()">Approve</button>
                </div>
              } @else if (a.note) {
                <small class="muted">Note: {{ a.note }}</small>
              }
            </div>
          </section>
        } @empty {
          <div class="card empty">
            <span class="empty-icon"><app-icon name="check-circle" [size]="20" /></span>
            <strong>Nothing waiting for approval</strong>
            <span>Refunds within the automatic limit run without one.</span>
          </div>
        }
      </div>
    </div>
  `,
})
export class ApprovalsPage {
  private readonly http = inject(HttpClient);
  private readonly auth = inject(Auth);

  protected showAll = false;
  protected notes: Record<string, string> = {};
  protected readonly busy = signal(false);
  protected readonly error = signal<string | null>(null);

  private readonly pendingOnly = signal(true);

  protected readonly approvals = httpResource<Approval[]>(() => {
    const orgId = this.auth.orgId();
    if (!orgId) return undefined;
    const params: Record<string, string> = this.pendingOnly() ? { status: 'pending' } : {};
    return { url: '/v1/approvals', params, headers: { 'X-Org-Id': orgId } };
  });

  protected amountOf(a: Approval): number {
    return Number(a.action['amount']);
  }

  protected reload(): void {
    this.pendingOnly.set(!this.showAll);
  }

  protected badge(status: Approval['status']): string {
    return status === 'approved' ? 'completed' : status === 'pending' ? 'queued' : 'failed';
  }

  protected async decide(a: Approval, approve: boolean): Promise<void> {
    this.busy.set(true);
    this.error.set(null);
    try {
      await firstValueFrom(
        this.http.post(`/v1/approvals/${a.id}/${approve ? 'approve' : 'reject'}`, {
          note: this.notes[a.id] || null,
        }),
      );
    } catch (err) {
      this.error.set(errorMessage(err));
    } finally {
      this.busy.set(false);
      this.approvals.reload();
    }
  }
}
