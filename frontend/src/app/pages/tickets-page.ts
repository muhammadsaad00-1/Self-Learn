import { DatePipe } from '@angular/common';
import { HttpClient, httpResource } from '@angular/common/http';
import { Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { firstValueFrom } from 'rxjs';

import type { Ticket } from '../api/types';
import { Auth } from '../core/auth';
import { errorMessage } from '../core/errors';

type View = 'open' | 'resolved' | 'all';

/** Cases a human must own: opened automatically for fraud, escalations, blocked or rejected
 *  actions. Open tickets are sorted most urgent first. */
@Component({
  selector: 'app-tickets-page',
  imports: [DatePipe, FormsModule],
  template: `
    <h1>Tickets</h1>
    <p class="muted">
      Opened automatically when the agents can't (or mustn't) resolve a case alone.
    </p>
    <div class="row">
      @for (v of views; track v) {
        <button type="button" class="link" [class.active-tab]="view() === v" (click)="view.set(v)">
          {{ v }}
        </button>
      }
    </div>
    @if (error()) {
      <p class="error" role="alert">{{ error() }}</p>
    }

    @for (t of tickets.value() ?? []; track t.id) {
      <section class="card section">
        <div class="answer-head">
          <strong>{{ t.code }}</strong>
          <span class="badge" [attr.data-urgency]="t.priority">{{ t.priority }}</span>
          <span class="badge" [attr.data-status]="t.status === 'resolved' ? 'completed' : 'queued'">
            {{ t.status }}
          </span>
          <small class="muted">
            {{ t.customer_ref ?? 'unknown customer' }} · {{ t.created_at | date: 'short' }}
            @if (t.assignee_id) {
              · {{ t.assignee_id === auth.me()?.user?.id ? 'assigned to you' : 'assigned' }}
            }
          </small>
        </div>
        <p>
          <strong>{{ t.title }}</strong>
        </p>
        <pre class="ticket-description">{{ t.description }}</pre>
        @if (t.status === 'resolved') {
          <p class="muted">
            <small>Resolution: {{ t.resolution_note }}</small>
          </p>
        } @else {
          <div class="row">
            @if (t.status === 'open') {
              <button type="button" (click)="update(t, 'take')" [disabled]="busy()">Take it</button>
            }
            <input
              [(ngModel)]="notes[t.id]"
              [name]="'note-' + t.id"
              placeholder="Resolution note"
              maxlength="2000"
            />
            <button
              type="button"
              (click)="update(t, 'resolve')"
              [disabled]="busy() || !notes[t.id]"
            >
              Resolve
            </button>
          </div>
        }
      </section>
    } @empty {
      <p class="muted">No tickets here.</p>
    }
  `,
})
export class TicketsPage {
  private readonly http = inject(HttpClient);
  protected readonly auth = inject(Auth);

  protected readonly views: View[] = ['open', 'resolved', 'all'];
  protected readonly view = signal<View>('open');
  protected notes: Record<string, string> = {};
  protected readonly busy = signal(false);
  protected readonly error = signal<string | null>(null);

  protected readonly tickets = httpResource<Ticket[]>(() => {
    const orgId = this.auth.orgId();
    return orgId
      ? { url: '/v1/tickets', params: { status: this.view() }, headers: { 'X-Org-Id': orgId } }
      : undefined;
  });

  protected async update(t: Ticket, action: 'take' | 'resolve'): Promise<void> {
    this.busy.set(true);
    this.error.set(null);
    try {
      await firstValueFrom(
        this.http.patch(`/v1/tickets/${t.id}`, { action, note: this.notes[t.id] || null }),
      );
    } catch (err) {
      this.error.set(errorMessage(err));
    } finally {
      this.busy.set(false);
      this.tickets.reload();
    }
  }
}
