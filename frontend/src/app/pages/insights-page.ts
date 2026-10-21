import { DecimalPipe, KeyValuePipe, PercentPipe } from '@angular/common';
import { httpResource } from '@angular/common/http';
import { Component, inject } from '@angular/core';

import type { Insights } from '../api/types';
import { Auth } from '../core/auth';

/** Impact at a glance: what the agents resolved alone, money returned, speed, AI cost. */
@Component({
  selector: 'app-insights-page',
  imports: [DecimalPipe, KeyValuePipe, PercentPipe],
  template: `
    <h1>Insights</h1>
    @if (insights.value(); as i) {
      <p class="muted">Last {{ i.days }} days · {{ i.requests }} customer requests</p>
      <div class="tiles">
        <div class="card tile hero">
          <span class="big">{{ i.ai_resolution_rate | percent: '1.0-0' }}</span>
          <span>resolved by AI end to end</span>
          <small class="muted"
            >{{ i.resolved_by_ai }} of {{ i.completed }} completed, no human needed</small
          >
        </div>
        <div class="card tile">
          <span class="big">PKR {{ +i.money_returned | number: '1.0-0' }}</span>
          <span>returned to customers</span>
          <small class="muted">refunds and failed-transfer reversals, ledger-verified</small>
        </div>
        <div class="card tile">
          <span class="big">{{ seconds(i.median_seconds_to_resolve) }}</span>
          <span>median time to resolve</span>
          <small class="muted">a branch visit or call centre: days</small>
        </div>
        <div class="card tile">
          <span class="big">{{ i.cards_frozen }}</span>
          <span>cards frozen on fraud</span>
          <small class="muted">within seconds of the report</small>
        </div>
        <div class="card tile">
          <span class="big">{{ i.waiting_approval }} · {{ i.open_tickets }}</span>
          <span>awaiting approval · open cases</span>
          <small class="muted">where humans stay in control</small>
        </div>
        <div class="card tile">
          <span class="big">\${{ +i.ai_cost_per_request_usd | number: '1.4-4' }}</span>
          <span>AI cost per request</span>
          <small class="muted"
            >\${{ +i.ai_cost_usd | number: '1.2-4' }} in total, every model call metered</small
          >
        </div>
      </div>

      <div class="split">
        <section class="card section">
          <h3>What customers ask about</h3>
          @for (c of i.by_category | keyvalue: byCount; track c.key) {
            <div class="bar-row">
              <span>{{ c.key.replaceAll('_', ' ') }}</span>
              <span class="bar"><i [style.width.%]="(100 * c.value) / i.requests"></i></span>
              <small>{{ c.value }}</small>
            </div>
          } @empty {
            <p class="muted">No requests yet.</p>
          }
        </section>
        <section class="card section">
          <h3>Channels</h3>
          @for (c of i.by_channel | keyvalue: byCount; track c.key) {
            <div class="bar-row">
              <span>{{ c.key }}</span>
              <span class="bar"><i [style.width.%]="(100 * c.value) / i.requests"></i></span>
              <small>{{ c.value }}</small>
            </div>
          }
        </section>
      </div>
    } @else if (insights.error()) {
      <p class="error" role="alert">Couldn't load insights.</p>
    }
  `,
})
export class InsightsPage {
  private readonly auth = inject(Auth);

  protected readonly insights = httpResource<Insights>(() =>
    this.auth.orgId() ? '/v1/insights' : undefined,
  );

  protected byCount(a: { value: number }, b: { value: number }): number {
    return b.value - a.value;
  }

  protected seconds(s: number | null): string {
    if (s === null) return '–';
    return s < 90 ? `${Math.round(s)} s` : `${Math.round(s / 60)} min`;
  }
}
