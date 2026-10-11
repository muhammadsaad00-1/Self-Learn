import { DatePipe, JsonPipe } from '@angular/common';
import { HttpClient } from '@angular/common/http';
import { Component, effect, inject, signal, untracked } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { firstValueFrom } from 'rxjs';

import type { AuditEvent, AuditPage as Page } from '../api/types';
import { Auth } from '../core/auth';
import { errorMessage } from '../core/errors';

@Component({
  selector: 'app-audit-page',
  imports: [DatePipe, JsonPipe, FormsModule],
  template: `
    <h1>Audit log</h1>
    <p class="muted">Every change in this organization. Entries can't be edited or deleted.</p>

    <form class="row" (ngSubmit)="load(true)">
      <input [(ngModel)]="action" name="action" placeholder="Filter by action, e.g. member.added" />
      <button type="submit">Filter</button>
    </form>

    @if (error()) {
      <p class="error" role="alert">{{ error() }}</p>
    }

    <table>
      <thead>
        <tr>
          <th>When</th>
          <th>Who</th>
          <th>Action</th>
          <th>Target</th>
          <th>Details</th>
        </tr>
      </thead>
      <tbody>
        @for (e of events(); track e.id) {
          <tr>
            <td>{{ e.occurred_at | date: 'medium' }}</td>
            <td>{{ e.actor_email ?? 'system' }}</td>
            <td>
              <code>{{ e.action }}</code>
            </td>
            <td>
              {{ e.target_type }} <code class="muted">{{ e.target_id.slice(0, 8) }}</code>
            </td>
            <td>
              <code>{{ e.details | json }}</code>
            </td>
          </tr>
        } @empty {
          <tr>
            <td colspan="5" class="muted">No events.</td>
          </tr>
        }
      </tbody>
    </table>

    @if (nextBeforeId()) {
      <button (click)="load(false)">Load older</button>
    }
  `,
})
export class AuditPage {
  private readonly http = inject(HttpClient);
  private readonly auth = inject(Auth);

  protected action = '';
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
