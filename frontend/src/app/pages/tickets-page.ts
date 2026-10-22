import { HttpClient, httpResource } from '@angular/common/http';
import { Component, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { firstValueFrom } from 'rxjs';

import type { Member, Ticket } from '../api/types';
import { Auth } from '../core/auth';
import { errorMessage } from '../core/errors';
import { FORMAT } from '../core/format';
import { Icon } from '../layout/icon';

type View = 'open' | 'mine' | 'resolved' | 'all';
type Action = 'take' | 'assign' | 'note' | 'resolve' | 'reopen';

/** One hand-off/note on a ticket (tickets.api: `activity`). */
interface TicketEvent {
  at: string;
  by: string;
  kind: Action;
  text: string | null;
  to?: string;
  customer_message?: string;
}

/** Cases a human must own: opened automatically for fraud, critical risk, escalations,
 *  blocked or rejected actions and automation failures. A queue (most urgent first) and one
 *  ticket in detail: why a human is needed, what the customer said, the hand-off trail, and
 *  the actions. Resolving can message the customer in their issue. */
@Component({
  selector: 'app-tickets-page',
  imports: [FormsModule, Icon, ...FORMAT],
  template: `
    <div class="page wide">
      <header class="page-header">
        <div>
          <h1>Tickets</h1>
          <p class="subtitle">Cases the agents handed to your team, most urgent first.</p>
        </div>
      </header>

      <div class="toolbar">
        <div class="segmented" role="tablist">
          @for (v of views; track v.id) {
            <button
              type="button"
              role="tab"
              [class.active]="view() === v.id"
              (click)="view.set(v.id)"
            >
              {{ v.label }}
            </button>
          }
        </div>
      </div>
      @if (error()) {
        <div class="callout" data-tone="danger" role="alert" style="margin-bottom: 12px">
          {{ error() }}
        </div>
      }

      <div class="split">
        <section class="card queue">
          @for (t of tickets.value() ?? []; track t.id) {
            <button
              type="button"
              class="queue-item"
              [class.selected]="t.id === selected()?.id"
              (click)="selectedId.set(t.id)"
            >
              <span
                class="prio"
                [attr.data-p]="t.status === 'resolved' ? 'done' : t.priority"
              ></span>
              <span class="stack-sm" style="gap: 4px; min-width: 0">
                <span class="line">
                  <small class="mono muted">{{ t.code }}</small>
                  <span class="title truncate">{{ t.title }}</span>
                  <span class="time">{{ t.created_at | ago }}</span>
                </span>
                <span class="line">
                  @if (t.category) {
                    <span class="badge">{{ t.category | human }}</span>
                  }
                  <small class="muted truncate">{{ t.customer_ref ?? 'Unknown customer' }}</small>
                  <span class="spacer"></span>
                  @if (t.status === 'resolved') {
                    <span class="badge" data-tone="success">Resolved</span>
                  } @else if (t.assignee_id) {
                    <span class="avatar sm" [title]="nameOf(t.assignee_id)">{{
                      nameOf(t.assignee_id) | initials
                    }}</span>
                  } @else {
                    <small class="faint">Unassigned</small>
                  }
                </span>
              </span>
            </button>
          } @empty {
            <div class="empty">
              <span class="empty-icon"><app-icon name="check-circle" [size]="20" /></span>
              <strong>{{ view() === 'open' ? 'Nothing needs a human' : 'No tickets here' }}</strong>
              <span>The agents open a ticket when a case needs your team.</span>
            </div>
          }
        </section>

        @if (selected(); as t) {
          <section class="card detail-sticky">
            <div class="card-head" style="align-items: flex-start">
              <div class="grow stack-sm">
                <div class="row">
                  <span class="mono muted">{{ t.code }}</span>
                  <span class="badge" [attr.data-tone]="t.priority | tone">{{
                    t.priority | human
                  }}</span>
                  <span class="badge dot" [attr.data-tone]="t.status | tone">{{
                    t.status | human
                  }}</span>
                </div>
                <h2>{{ t.title }}</h2>
                <small class="muted">
                  {{ t.customer_ref ?? 'Unknown customer' }}
                  @if (t.category) {
                    · {{ t.category | human }}
                  }
                  · opened {{ t.created_at | ago }}
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
            </div>

            <div class="card-body stack">
              <div
                class="callout"
                [attr.data-tone]="t.priority === 'critical' ? 'danger' : 'warning'"
              >
                <app-icon name="alert" />
                <div class="stack-sm">
                  <strong>Why a human is needed</strong>
                  <ul class="reasons" style="color: inherit">
                    @for (r of reasons(t); track r) {
                      <li>{{ r }}</li>
                    }
                  </ul>
                </div>
              </div>

              @if (t.customer_message) {
                <div>
                  <h3 class="section-title">The customer said</h3>
                  <p class="quote">{{ t.customer_message }}</p>
                </div>
              }

              @if (events(t).length) {
                <div>
                  <h3 class="section-title">Activity</h3>
                  <ol class="timeline">
                    @for (e of events(t); track $index) {
                      <li>
                        <span
                          class="node"
                          [attr.data-tone]="e.kind === 'resolve' ? 'success' : 'brand'"
                        >
                          <app-icon [name]="eventIcon[e.kind]" [size]="14" />
                        </span>
                        <div class="body">
                          <span>
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
                                added a note
                              }
                            }
                            <small class="faint"> · {{ e.at | ago }}</small>
                          </span>
                          @if (e.text) {
                            <span class="muted">“{{ e.text }}”</span>
                          }
                          @if (e.customer_message) {
                            <small class="muted"
                              >Told the customer: “{{ e.customer_message }}”</small
                            >
                          }
                        </div>
                      </li>
                    }
                  </ol>
                </div>
              }

              @if (t.status === 'resolved') {
                <div class="callout" data-tone="brand">
                  <app-icon name="check-circle" />
                  <span>Resolved {{ t.resolved_at | ago }}: {{ t.resolution_note }}</span>
                </div>
                <div class="row">
                  <button
                    type="button"
                    class="secondary"
                    (click)="update(t, 'reopen')"
                    [disabled]="busy()"
                  >
                    Reopen
                  </button>
                </div>
              } @else {
                <div class="stack" style="padding-top: 4px; border-top: 1px solid var(--border)">
                  <div class="row" style="padding-top: 12px">
                    @if (t.assignee_id !== auth.me()?.user?.id) {
                      <button
                        type="button"
                        class="secondary"
                        (click)="update(t, 'take')"
                        [disabled]="busy()"
                      >
                        <app-icon name="hand" /> Take it
                      </button>
                    }
                    <select
                      [(ngModel)]="assignees[t.id]"
                      [name]="'to-' + t.id"
                      aria-label="Hand off to"
                    >
                      <option [ngValue]="undefined">Hand off to…</option>
                      @for (m of members.value() ?? []; track m.user_id) {
                        @if (m.user_id !== t.assignee_id) {
                          <option [ngValue]="m.user_id">{{ m.full_name }}</option>
                        }
                      }
                    </select>
                    <button
                      type="button"
                      class="secondary"
                      (click)="update(t, 'assign')"
                      [disabled]="busy() || !assignees[t.id]"
                    >
                      Hand off
                    </button>
                  </div>
                  <label>
                    Note
                    <input
                      [(ngModel)]="notes[t.id]"
                      [name]="'note-' + t.id"
                      placeholder="Note: hand-off context, or how it was resolved"
                      maxlength="2000"
                    />
                  </label>
                  @if (t.customer_ref) {
                    <label>
                      Message to the customer
                      <span class="field-hint"
                        >optional · appears in their issue when you resolve</span
                      >
                      <textarea
                        [(ngModel)]="customerMessages[t.id]"
                        [name]="'cm-' + t.id"
                        rows="2"
                        placeholder="Message to the customer, e.g. Good news: the PKR 85,000 has been credited back."
                        maxlength="2000"
                      ></textarea>
                    </label>
                  }
                  <div class="row">
                    <button
                      type="button"
                      class="secondary"
                      (click)="update(t, 'note')"
                      [disabled]="busy() || !notes[t.id]"
                    >
                      Add note
                    </button>
                    <span class="spacer"></span>
                    <button
                      type="button"
                      (click)="update(t, 'resolve')"
                      [disabled]="busy() || !notes[t.id]"
                      title="A note saying how it was resolved is required"
                    >
                      <app-icon name="check" /> Resolve
                    </button>
                  </div>
                </div>
              }
            </div>
          </section>
        } @else {
          <section class="card empty">
            <span class="empty-icon"><app-icon name="ticket" [size]="20" /></span>
            <strong>Select a ticket</strong>
          </section>
        }
      </div>
    </div>
  `,
})
export class TicketsPage {
  private readonly http = inject(HttpClient);
  protected readonly auth = inject(Auth);

  protected readonly views: { id: View; label: string }[] = [
    { id: 'open', label: 'Open' },
    { id: 'mine', label: 'Mine' },
    { id: 'resolved', label: 'Resolved' },
    { id: 'all', label: 'All' },
  ];
  protected readonly eventIcon: Record<Action, string> = {
    take: 'hand',
    assign: 'users',
    note: 'message',
    resolve: 'check',
    reopen: 'activity',
  };
  protected readonly selectedId = signal<string | null>(null);
  /** The chosen ticket, else the first in the queue. */
  protected readonly selected = computed(() => {
    const list = this.tickets.value() ?? [];
    return list.find((t) => t.id === this.selectedId()) ?? list[0] ?? null;
  });
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

  /** Structured reasons; older tickets only have them inside the description text. */
  protected reasons(t: Ticket): string[] {
    if (t.reasons.length) return t.reasons;
    const lines = t.description.split('\n');
    return lines.filter((l) => l.startsWith('- ')).map((l) => l.slice(2));
  }

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
