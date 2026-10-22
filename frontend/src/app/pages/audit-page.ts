import { DatePipe } from '@angular/common';
import { HttpClient } from '@angular/common/http';
import { Component, effect, inject, signal, untracked } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { firstValueFrom } from 'rxjs';

import type { AuditEvent, AuditPage as Page } from '../api/types';
import { Auth } from '../core/auth';
import { errorMessage } from '../core/errors';
import { FORMAT } from '../core/format';
import { Icon } from '../layout/icon';

/** Every change in the company, append-only: what compliance reads. */
@Component({
  selector: 'app-audit-page',
  imports: [DatePipe, FormsModule, Icon, ...FORMAT],
  template: `
    <div class="page">
      <header class="page-header">
        <div>
          <h1>Audit log</h1>
          <p class="subtitle">
            Every change and every action the agents executed. Entries can't be edited or deleted.
          </p>
        </div>
      </header>

      <form class="toolbar" (ngSubmit)="load(true)">
        <input
          [(ngModel)]="action"
          name="action"
          placeholder="Filter by action, e.g. tool.refund_executed"
          style="width: 320px"
        />
        <button type="submit" class="secondary"><app-icon name="search" /> Filter</button>
      </form>

      @if (error()) {
        <div class="callout" data-tone="danger" role="alert" style="margin-bottom: 12px">
          {{ error() }}
        </div>
      }

      <section class="card table-wrap">
        <table>
          <thead>
            <tr>
              <th>When</th>
              <th>Who</th>
              <th>What</th>
              <th>Details</th>
            </tr>
          </thead>
          <tbody>
            @for (e of events(); track e.id) {
              <tr>
                <td style="white-space: nowrap">
                  {{ e.occurred_at | ago }}
                  <div>
                    <small class="faint">{{ e.occurred_at | date: 'MMM d, HH:mm:ss' }}</small>
                  </div>
                </td>
                <td>
                  @if (e.actor_email) {
                    {{ e.actor_email }}
                  } @else {
                    <span class="badge" data-tone="ai"
                      ><app-icon name="bot" [size]="12" /> AgentFlow</span
                    >
                  }
                </td>
                <td style="min-width: 200px">
                  <strong>{{ e.action | human }}</strong>
                  <div>
                    <small class="faint"
                      >{{ e.target_type }}
                      <span class="mono">{{ e.target_id.slice(0, 12) }}</span></small
                    >
                  </div>
                </td>
                <td>
                  <div class="row" style="gap: 4px">
                    @for (kv of entries(e.details); track kv[0]) {
                      <span class="badge" [title]="kv[1]"
                        ><span class="faint">{{ kv[0] | human }}</span
                        >&nbsp;{{ kv[1].slice(0, 40) }}</span
                      >
                    }
                  </div>
                </td>
              </tr>
            } @empty {
              <tr>
                <td colspan="4">
                  <div class="empty"><strong>No events</strong></div>
                </td>
              </tr>
            }
          </tbody>
        </table>
      </section>

      @if (nextBeforeId()) {
        <div class="row" style="justify-content: center; margin-top: 16px">
          <button class="secondary" (click)="load(false)">Load older</button>
        </div>
      }
    </div>
  `,
})
export class AuditPage {
  private readonly http = inject(HttpClient);
  private readonly auth = inject(Auth);

  protected action = '';

  /** Details as readable key/value pairs (nested values as JSON). */
  protected entries(details: Record<string, unknown> | null | undefined): [string, string][] {
    return Object.entries(details ?? {}).map(([k, v]) => [
      k,
      typeof v === 'string' ? v : JSON.stringify(v),
    ]);
  }
  protected readonly events = signal<AuditEvent[]>([]);
  protected readonly nextBeforeId = signal<number | null>(null);
  protected readonly error = signal<string | null>(null);

  constructor() {
    // Start over whenever the selected org changes (and once on first render). No org means
    // the user is signing out: don't fire a request that can only fail.
    effect(() => {
      if (this.auth.orgId()) untracked(() => void this.load(true));
    });
  }

  /** First page (reset) or the next older page, using the server's keyset cursor. */
  protected async load(reset: boolean): Promise<void> {
    const params: Record<string, string | number> = { limit: 50 };
    if (this.action.trim()) params['action'] = this.action.trim();
    const before = this.nextBeforeId();
    if (!reset && before) params['before_id'] = before;
    this.error.set(null);
    try {
      const page = await firstValueFrom(this.http.get<Page>('/v1/audit/events', { params }));
      this.events.set(reset ? page.events : [...this.events(), ...page.events]);
      this.nextBeforeId.set(page.next_before_id ?? null);
    } catch (err) {
      this.error.set(errorMessage(err));
    }
  }
}
