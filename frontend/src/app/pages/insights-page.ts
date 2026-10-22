import { DecimalPipe, KeyValuePipe, PercentPipe } from '@angular/common';
import { httpResource } from '@angular/common/http';
import { Component, inject } from '@angular/core';

import type { Insights } from '../api/types';
import { Auth } from '../core/auth';
import { HumanPipe } from '../core/format';
import { Icon } from '../layout/icon';

/** Impact at a glance: what the agents resolved alone, money returned, speed, AI cost. */
@Component({
  selector: 'app-insights-page',
  imports: [DecimalPipe, KeyValuePipe, PercentPipe, Icon, HumanPipe],
  template: `
    <div class="page">
      <header class="page-header">
        <div>
          <h1>Overview</h1>
          <p class="subtitle">
            What the agents handled in the last {{ insights.value()?.days ?? 30 }} days, and where
            your team stepped in.
          </p>
        </div>
      </header>

      @if (insights.value(); as i) {
        @if (i.requests === 0) {
          <div class="card empty">
            <span class="empty-icon"><app-icon name="sparkles" [size]="20" /></span>
            <strong>No customer requests yet</strong>
            <span>Load demo customers on Requests, then open the customer app from the Inbox.</span>
          </div>
        } @else {
          <div class="tiles">
            <div class="card tile hero">
              <span class="tile-head"
                ><span class="tile-icon"><app-icon name="bot" /></span> Resolved by AI</span
              >
              <span class="big">{{ i.ai_resolution_rate | percent: '1.0-0' }}</span>
              <small class="muted"
                >{{ i.resolved_by_ai }} of {{ i.completed }} completed with no human</small
              >
            </div>
            <div class="card tile">
              <span class="tile-head"
                ><span class="tile-icon"><app-icon name="money" /></span> Returned to
                customers</span
              >
              <span class="big">PKR {{ +i.money_returned | number: '1.0-0' }}</span>
              <small class="muted">Refunds and failed-transfer reversals, ledger-verified</small>
            </div>
            <div class="card tile">
              <span class="tile-head"
                ><span class="tile-icon"><app-icon name="clock" /></span> Median time to
                resolve</span
              >
              <span class="big">{{ seconds(i.median_seconds_to_resolve) }}</span>
              <small class="muted">For requests the AI resolved alone</small>
            </div>
            <div class="card tile">
              <span class="tile-head"
                ><span class="tile-icon"><app-icon name="lock" /></span> Cards frozen on fraud</span
              >
              <span class="big">{{ i.cards_frozen }}</span>
              <small class="muted">Within seconds of the report</small>
            </div>
            <div class="card tile">
              <span class="tile-head"
                ><span class="tile-icon"><app-icon name="hand" /></span> Waiting for your team</span
              >
              <span class="big">{{ i.open_tickets + i.waiting_approval }}</span>
              <small class="muted"
                >{{ i.open_tickets }} open tickets · {{ i.waiting_approval }} approvals</small
              >
            </div>
            <div class="card tile">
              <span class="tile-head"
                ><span class="tile-icon"><app-icon name="sparkles" /></span> AI cost per
                request</span
              >
              <span class="big">\${{ +i.ai_cost_per_request_usd | number: '1.4-4' }}</span>
              <small class="muted"
                >\${{ +i.ai_cost_usd | number: '1.2-4' }} in total · every model call metered</small
              >
            </div>
          </div>

          <div class="split reverse">
            <section class="card pad">
              <h2 style="margin-bottom: 12px">What customers ask about</h2>
              @for (c of i.by_category | keyvalue: byCount; track c.key) {
                <div class="bar-row">
                  <span>{{ c.key | human }}</span>
                  <span class="bar"><i [style.width.%]="(100 * c.value) / i.requests"></i></span>
                  <small class="muted">{{ c.value }}</small>
                </div>
              }
            </section>
            <section class="card pad">
              <h2 style="margin-bottom: 12px">Channels</h2>
              @for (c of i.by_channel | keyvalue: byCount; track c.key) {
                <div class="bar-row">
                  <span>{{ c.key === 'chat' ? 'Customer app' : (c.key | human) }}</span>
                  <span class="bar"><i [style.width.%]="(100 * c.value) / i.requests"></i></span>
                  <small class="muted">{{ c.value }}</small>
                </div>
              }
            </section>
          </div>
        }
      } @else if (insights.error()) {
        <div class="callout" data-tone="danger" role="alert">Couldn't load the overview.</div>
      } @else {
        <div class="tiles">
          @for (n of [1, 2, 3, 4, 5, 6]; track n) {
            <div class="card tile">
              <div class="skeleton" style="width: 50%"></div>
              <div class="skeleton" style="height: 28px; width: 70%"></div>
            </div>
          }
        </div>
      }
    </div>
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
