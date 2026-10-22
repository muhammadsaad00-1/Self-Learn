import { DatePipe } from '@angular/common';
import { HttpClient, httpResource } from '@angular/common/http';
import { Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { firstValueFrom } from 'rxjs';

import type { Member, Ticket } from '../api/types';
import { Auth } from '../core/auth';
import { errorMessage } from '../core/errors';

type View = 'open' | 'mine' | 'resolved' | 'all';
type Action = 'take' | 'assign' | 'note' | 'resolve';

/** One hand-off/note on a ticket (tickets.api: `activity`). */
interface TicketEvent {
  at: string;
  by: string;
  kind: Action | 'reopen';
  text: string | null;
  to?: string;
  customer_message?: string;
}

/** Cases a human must own: opened automatically for fraud, critical risk, escalations,
 *  blocked or rejected actions. Open tickets are sorted most urgent first. Teammates hand
 *  cases to each other with a note; resolving can message the customer in their chat. */
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
              ·
              {{
                t.assignee_id === auth.me()?.user?.id
                  ? 'assigned to you'
                  : 'with ' + nameOf(t.assignee_id)
              }}
            }
          </small>
        </div>
        <p>
          <strong>{{ t.title }}</strong>
        </p>
        <pre class="ticket-description">{{ t.description }}</pre>
        @if (t.activity.length) {
          <ol class="activity-trail">
            @for (e of events(t); track $index) {
              <li>
                <small class="muted">{{ e.at | date: 'short' }}</small>
                <strong>{{ e.by }}</strong>
                @switch (e.kind) {
                  @case ('assign') {
                    handed it to <strong>{{ e.to }}</strong>
                  }
                  @case ('take') {
                    took it
                  }
                  @case ('resolve') {
                    resolved it
                  }
                  @case ('reopen') {
                    reopened it
                  }
                  @default {
                    noted
                  }
                }
                @if (e.text) {
                  : “{{ e.text }}”
                }
                @if (e.customer_message) {
                  <br /><small class="muted">Told the customer: “{{ e.customer_message }}”</small>
                }
              </li>
            }
          </ol>
        }
        @if (t.status === 'resolved') {
          <p class="muted">
            <small>Resolution: {{ t.resolution_note }}</small>
          </p>
        } @else {
          <div class="row">
            <input
              [(ngModel)]="notes[t.id]"
              [name]="'note-' + t.id"
              placeholder="Note (hand-off context, or how it was resolved)"
              maxlength="2000"
            />
          </div>
          <div class="row">
            @if (t.status === 'open') {
              <button type="button" (click)="update(t, 'take')" [disabled]="busy()">Take it</button>
            }
            <select [(ngModel)]="assignees[t.id]" [name]="'to-' + t.id" aria-label="Hand off to">
              <option [ngValue]="undefined">Hand off to…</option>
              @for (m of members.value() ?? []; track m.user_id) {
                @if (m.user_id !== t.assignee_id) {
                  <option [ngValue]="m.user_id">{{ m.full_name }}</option>
                }
              }
            </select>
            <button
              type="button"
              (click)="update(t, 'assign')"
              [disabled]="busy() || !assignees[t.id]"
            >
              Hand off
            </button>
            <button
              type="button"
              class="link"
              (click)="update(t, 'note')"
              [disabled]="busy() || !notes[t.id]"
            >
              Add note
            </button>
          </div>
          @if (t.customer_ref) {
            <div class="row">
              <input
                [(ngModel)]="customerMessages[t.id]"
                [name]="'cm-' + t.id"
                placeholder="Message to the customer when resolving (optional, sent to their chat)"
                maxlength="2000"
              />
            </div>
          }
          <div class="row">
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

  protected readonly views: View[] = ['open', 'mine', 'resolved', 'all'];
  protected readonly view = signal<View>('open');
  protected notes: Record<string, string> = {};
  protected assignees: Record<string, string | undefined> = {};
  protected customerMessages: Record<string, string> = {};
  protected readonly busy = signal(false);
  protected readonly error = signal<string | null>(null);

  protected readonly tickets = httpResource<Ticket[]>(() => {
    const orgId = this.auth.orgId();
    return orgId
      ? {
          url: '/v1/tickets',
          params: (this.view() === 'mine'
            ? { status: 'open', mine: 'true' }
            : { status: this.view() }) as Record<string, string>,
          headers: { 'X-Org-Id': orgId },
        }
      : undefined;
  });

  protected readonly members = httpResource<Member[]>(() => {
    const orgId = this.auth.orgId();
    return orgId ? { url: '/v1/members', headers: { 'X-Org-Id': orgId } } : undefined;
  });

  protected events(t: Ticket): TicketEvent[] {
    return t.activity as unknown as TicketEvent[];
  }

  protected nameOf(userId: string): string {
    return this.members.value()?.find((m) => m.user_id === userId)?.full_name ?? 'a teammate';
  }

  protected async update(t: Ticket, action: Action): Promise<void> {
    this.busy.set(true);
    this.error.set(null);
    try {
      await firstValueFrom(
        this.http.patch(`/v1/tickets/${t.id}`, {
          action,
          note: this.notes[t.id] || null,
          assignee_id: action === 'assign' ? this.assignees[t.id] : null,
          customer_message: action === 'resolve' ? this.customerMessages[t.id] || null : null,
        }),
      );
      delete this.notes[t.id];
      delete this.assignees[t.id];
    } catch (err) {
      this.error.set(errorMessage(err));
    } finally {
      this.busy.set(false);
      this.tickets.reload();
    }
  }
}
