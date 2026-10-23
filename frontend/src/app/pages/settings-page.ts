import { HttpClient, httpResource } from '@angular/common/http';
import { Component, effect, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { firstValueFrom } from 'rxjs';

import type { BusinessProfile, InboundAddress, OrgProfile } from '../api/types';
import { Auth } from '../core/auth';
import { errorMessage } from '../core/errors';
import { HumanPipe } from '../core/format';
import { Icon } from '../layout/icon';

const INDUSTRIES: BusinessProfile['industry'][] = [
  'commercial_bank',
  'microfinance_bank',
  'digital_wallet',
  'digital_lender',
];

/** Company settings (admins): the business profile from signup, the support email inbox and
 *  the customer app link. */
@Component({
  selector: 'app-settings-page',
  imports: [FormsModule, Icon, HumanPipe],
  template: `
    <div class="page" style="max-width: 880px">
      <header class="page-header">
        <div>
          <h1>Settings</h1>
          <p class="subtitle">How customers reach you, and the numbers behind your Overview.</p>
        </div>
      </header>

      <section class="card" style="margin-bottom: 16px">
        <div class="card-head">
          <span class="avatar square"><app-icon name="message" /></span>
          <div class="grow">
            <h2>Support email</h2>
            <small class="muted"
              >Forward your support mailbox here (SendGrid Inbound Parse, Mailgun routes, Gmail
              forwarding via a relay). Every email becomes a request the agents handle.</small
            >
          </div>
        </div>
        <div class="card-body stack">
          @if (address.value(); as a) {
            <div class="row" style="flex-wrap: nowrap">
              <input
                class="grow mono"
                [value]="a.url"
                readonly
                aria-label="Inbound email address"
              />
              <button type="button" class="secondary" (click)="copy(a.url)">
                <app-icon [name]="copied() ? 'check' : 'external'" />
                {{ copied() ? 'Copied' : 'Copy' }}
              </button>
            </div>
            <small class="muted">
              JSON body:
              <code>{{ '{' }}"from_address", "subject", "text", "message_id"{{ '}' }}</code
              >. Senders are matched to customers by email (demo customers:
              {{ a.sample_customer_emails[0] }} … {{ a.sample_customer_emails.at(-1) }}).
            </small>
          }
          <div class="row">
            <button type="button" class="secondary" (click)="sampleInbox()" [disabled]="busy()">
              <app-icon name="inbox" /> Load a sample inbox (8 emails)
            </button>
            @if (inboxLoaded()) {
              <span class="badge" data-tone="success"
                >{{ inboxLoaded() }} emails queued: see Requests</span
              >
            }
          </div>
        </div>
      </section>

      <section class="card">
        <div class="card-head">
          <span class="avatar square"><app-icon name="building" /></span>
          <div class="grow">
            <h2>Business profile</h2>
            <small class="muted">From signup. Sizes the savings on your Overview.</small>
          </div>
        </div>
        <form class="card-body stack" (ngSubmit)="save()">
          <div class="row" style="flex-wrap: nowrap">
            <label class="grow">
              Industry
              <select [(ngModel)]="draft.industry" name="industry">
                @for (i of industries; track i) {
                  <option [value]="i">{{ i | human }}</option>
                }
              </select>
            </label>
            <label class="grow"
              >Customer messages / month
              <input [(ngModel)]="draft.monthly_queries" name="mq" type="number" min="0"
            /></label>
          </div>
          <div class="row" style="flex-wrap: nowrap">
            <label class="grow"
              >Minutes a person spends per query
              <input [(ngModel)]="draft.minutes_per_query" name="mpq" type="number" min="1"
            /></label>
            <label class="grow"
              >Agent cost per hour ({{ draft.currency }})
              <input [(ngModel)]="draft.agent_cost_per_hour" name="cph" type="number" min="0"
            /></label>
          </div>
          @if (error()) {
            <div class="callout" data-tone="danger" role="alert">{{ error() }}</div>
          }
          <div class="row">
            @if (saved()) {
              <span class="badge" data-tone="success"
                ><app-icon name="check" [size]="12" /> Saved</span
              >
            }
            <span class="spacer"></span>
            <button type="submit" [disabled]="busy()">Save profile</button>
          </div>
        </form>
      </section>
    </div>
  `,
})
export class SettingsPage {
  private readonly http = inject(HttpClient);
  private readonly auth = inject(Auth);

  protected readonly industries = INDUSTRIES;
  protected readonly busy = signal(false);
  protected readonly error = signal<string | null>(null);
  protected readonly saved = signal(false);
  protected readonly copied = signal(false);
  protected readonly inboxLoaded = signal(0);
  protected draft: BusinessProfile = {
    industry: 'commercial_bank',
    country: 'PK',
    currency: 'PKR',
    customers: '10k_100k',
    monthly_queries: 5000,
    channels: ['app_chat'],
    languages: ['en', 'roman_ur'],
    minutes_per_query: 8,
    agent_cost_per_hour: 1500,
  };

  protected readonly profile = httpResource<OrgProfile>(() =>
    this.auth.orgId() ? '/v1/orgs/current/profile' : undefined,
  );
  protected readonly address = httpResource<InboundAddress>(() =>
    this.auth.orgId() ? '/v1/inbound/address' : undefined,
  );

  constructor() {
    effect(() => {
      const p = this.profile.value()?.profile;
      if (p) this.draft = { ...p };
    });
  }

  protected async copy(text: string): Promise<void> {
    await navigator.clipboard.writeText(text).catch(() => undefined);
    this.copied.set(true);
    setTimeout(() => this.copied.set(false), 2000);
  }

  protected async sampleInbox(): Promise<void> {
    await this.run(async () => {
      await firstValueFrom(this.http.post('/v1/ledger/demo-data', {})); // senders to match
      const accepted = await firstValueFrom(this.http.post<unknown[]>('/v1/inbound/sample', {}));
      this.inboxLoaded.set(accepted.length);
    });
  }

  protected async save(): Promise<void> {
    await this.run(async () => {
      await firstValueFrom(this.http.put('/v1/orgs/current/profile', this.draft));
      this.saved.set(true);
      setTimeout(() => this.saved.set(false), 2500);
    });
  }

  private async run(action: () => Promise<void>): Promise<void> {
    this.busy.set(true);
    this.error.set(null);
    try {
      await action();
    } catch (err) {
      this.error.set(errorMessage(err));
    } finally {
      this.busy.set(false);
    }
  }
}
