import { DatePipe } from '@angular/common';
import { HttpClient, httpResource } from '@angular/common/http';
import { Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { firstValueFrom } from 'rxjs';

import type { Approval } from '../api/types';
import { Auth } from '../core/auth';
import { errorMessage } from '../core/errors';

/** Maker-checker inbox: actions the policy engine won't run without a second officer. */
@Component({
  selector: 'app-approvals-page',
  imports: [DatePipe, FormsModule, RouterLink],
  template: `
    <h1>Approvals</h1>
    <p class="muted">
      Actions above the automatic limits. Maker-checker: you can't approve a request you submitted
      yourself.
    </p>
    <div class="row">
      <label
        ><input type="checkbox" [(ngModel)]="showAll" (change)="reload()" /> show decided</label
      >
    </div>
    @if (error()) {
      <p class="error" role="alert">{{ error() }}</p>
    }

    @for (a of approvals.value() ?? []; track a.id) {
      <section class="card section approval">
        <div class="answer-head">
          <span class="badge" [attr.data-status]="badge(a.status)">{{ a.status }}</span>
          @if (a.urgency) {
            <span class="badge" [attr.data-urgency]="a.urgency">{{ a.urgency }}</span>
          }
          <small class="muted">
            {{ a.customer_ref }} · {{ a.category }} · requested {{ a.requested_at | date: 'short' }}
            @if (a.status === 'pending') {
              · expires {{ a.expires_at | date: 'short' }}
            }
          </small>
        </div>
        <p>"{{ a.message_preview }}"</p>
        <p>
          <strong>Refund {{ a.action['amount'] }}</strong> for
          <code>{{ a.action['transaction_reference'] }}</code>
          <br /><small class="muted">{{ a.action['rationale'] }}</small>
        </p>
        <ul class="reasons">
          @for (r of a.policy_reasons; track r) {
            <li>{{ r }}</li>
          }
        </ul>
        @if (a.status === 'pending') {
          <div class="row">
            <input
              [(ngModel)]="notes[a.id]"
              [name]="'note-' + a.id"
              placeholder="Note (optional)"
              maxlength="500"
            />
            <button (click)="decide(a, true)" [disabled]="busy()">Approve</button>
            <button class="danger-button" (click)="decide(a, false)" [disabled]="busy()">
              Reject
            </button>
          </div>
        } @else if (a.note) {
          <p class="muted">
            <small>Note: {{ a.note }}</small>
          </p>
        }
        <small><a routerLink="/workflows">Open workflows</a></small>
      </section>
    } @empty {
      <p class="muted">Nothing waiting for approval.</p>
    }
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
