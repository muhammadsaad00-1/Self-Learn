import { DecimalPipe, KeyValuePipe, PercentPipe } from '@angular/common';
import { HttpClient, httpResource } from '@angular/common/http';
import { Component, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { firstValueFrom } from 'rxjs';

import type { Insights } from '../api/types';
import { Auth } from '../core/auth';
import { errorMessage } from '../core/errors';
import { HumanPipe } from '../core/format';
import { Icon } from '../layout/icon';

/** Impact at a glance: what the agents resolved alone, money returned, speed, AI cost. */
@Component({
  selector: 'app-insights-page',
  imports: [DecimalPipe, KeyValuePipe, PercentPipe, Icon, HumanPipe, RouterLink],
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
          <section class="card">
            <div class="card-head">
              <span class="empty-icon" style="margin: 0"
                ><app-icon name="sparkles" [size]="18"
              /></span>
              <div class="grow">
                <h2>Get started</h2>
                <small class="muted"
                  >Four steps to see the agents work on your customers' requests.</small
                >
              </div>
            </div>
            <ol class="checklist">
              <li>
                <span class="num">1</span>
                <div class="grow">
                  <strong>Add your customers' accounts</strong>
                  <div class="muted">
                    Demo: six customers with real-looking histories, failed transfers and a disputed
                    payment.
                  </div>
                </div>
                @if (auth.isAdmin()) {
                  <button class="secondary" (click)="setup('demo')" [disabled]="busy()">
                    Load demo customers
                  </button>
                }
              </li>
              <li>
                <span class="num">2</span>
                <div class="grow">
                  <strong>Upload your policies and FAQ</strong>
                  <div class="muted">The agents answer only from your documents, with sources.</div>
                </div>
                <a class="button secondary" routerLink="/knowledge">Upload</a>
              </li>
              <li>
                <span class="num">3</span>
                <div class="grow">
                  <strong>Forward your support email</strong>
                  <div class="muted">
                    Or load a sample inbox of 8 real-world emails, including a fraud claim and an
                    injection attempt.
                  </div>
                </div>
                @if (auth.isAdmin()) {
                  <button class="secondary" (click)="setup('inbox')" [disabled]="busy()">
                    Load sample inbox
                  </button>
                }
              </li>
              <li>
                <span class="num">4</span>
                <div class="grow">
                  <strong>Open your customer app</strong>
                  <div class="muted">
                    Text or send a voice note as a customer, in English or Urdu.
                  </div>
                </div>
                <a class="button secondary" routerLink="/chats">Open inbox</a>
              </li>
            </ol>
            @if (setupError()) {
              <div class="card-body">
                <div class="callout" data-tone="danger">{{ setupError() }}</div>
              </div>
            }
          </section>
        } @else {
          <section class="card roi">
            <div>
              <small class="muted">Your team saved so far</small>
              <span class="big">{{ i.team_hours_saved | number: '1.0-1' }} h</span>
              <small class="muted"
                >≈ {{ i.currency }} {{ +i.team_cost_saved | number: '1.0-0' }}</small
              >
            </div>
            <div>
              <small class="muted">At {{ i.monthly_queries | number }} queries a month</small>
              <span class="big"
                >{{ i.projected_monthly_hours_saved | number: '1.0-0' }} h / month</span
              >
              <small class="muted"
                >≈ {{ i.currency }} {{ +i.projected_monthly_cost_saved | number: '1.0-0' }} a month
                · {{ i.projected_monthly_hours_saved / 160 | number: '1.0-1' }} full-time
                agents</small
              >
            </div>
            <small class="faint" style="grid-column: 1 / -1"
              >Assumes {{ i.assumptions }} (from your business profile, editable in
              Settings).</small
            >
          </section>
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
  protected readonly auth = inject(Auth);
  private readonly http = inject(HttpClient);
  protected readonly busy = signal(false);
  protected readonly setupError = signal<string | null>(null);

  /** One-click setup for a demo: customers (and the help guide), or the sample inbox. */
  protected async setup(what: 'demo' | 'inbox'): Promise<void> {
    this.busy.set(true);
    this.setupError.set(null);
    try {
      if (what === 'inbox') await firstValueFrom(this.http.post('/v1/ledger/demo-data', {}));
      await firstValueFrom(
        this.http.post(what === 'demo' ? '/v1/ledger/demo-data' : '/v1/inbound/sample', {}),
      );
      this.insights.reload();
    } catch (err) {
      this.setupError.set(errorMessage(err));
    } finally {
      this.busy.set(false);
    }
  }

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
