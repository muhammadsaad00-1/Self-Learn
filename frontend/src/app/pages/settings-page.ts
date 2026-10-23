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
  'transport',
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

      <section class="card" style="margin-bottom: 16px">
        <div class="card-head">
          <span class="avatar square"><app-icon name="shield" /></span>
          <div class="grow">
            <h2>Action policy limits</h2>
            <small class="muted">
              Deterministic guardrails applied before proposed refunds. Changes are versioned and
              recorded in the audit log. Defaults: {{ draft.currency }} 2,000 auto-approval,
              {{ draft.currency }} 100,000 maximum, 3 reversals per day.
            </small>
          </div>
        </div>
        <form class="card-body stack" (ngSubmit)="savePolicy()">
          <div class="row">
            <label class="grow"
              >Auto-approval limit ({{ draft.currency }})
              <input
                [(ngModel)]="policyDraft.auto_approve_limit"
                name="autoApproveLimit"
                type="number"
                min="0"
                step="0.01"
              />
            </label>
            <label class="grow"
              >Maximum automated refund ({{ draft.currency }})
              <input
                [(ngModel)]="policyDraft.max_refund"
                name="maxRefund"
                type="number"
                min="0.01"
                step="0.01"
              />
            </label>
            <label class="grow"
              >Reversals allowed per 24 hours
              <input
                [(ngModel)]="policyDraft.max_reversals_per_day"
                name="maxReversals"
                type="number"
                min="1"
                max="100"
                step="1"
              />
            </label>
          </div>
          @if (policyError()) {
            <div class="callout" data-tone="danger" role="alert">{{ policyError() }}</div>
          }
          <div class="row">
            <small class="muted">Policy version {{ policy.value()?.version ?? 0 }}</small>
            <span class="spacer"></span>
            <button type="submit" [disabled]="policyBusy()">
              {{ policyBusy() ? 'Saving…' : 'Save action policy' }}
            </button>
          </div>
        </form>
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
          <label>
            Time zone
            <input [(ngModel)]="draft.timezone" name="tz" placeholder="Asia/Karachi" />
            <span class="field-hint"
              >Imported times without a zone are read in it; customers hear times in it.</span
            >
          </label>
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
  protected readonly policyBusy = signal(false);
  protected readonly policyError = signal<string | null>(null);
  protected policyDraft = {
    auto_approve_limit: 2000,
    max_refund: 100000,
    max_reversals_per_day: 3,
  };
  protected draft: BusinessProfile = {
    industry: 'commercial_bank',
    country: 'PK',
    currency: 'PKR',
    timezone: 'Asia/Karachi',
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
  protected readonly policy = httpResource<{
    auto_approve_limit: number;
    max_refund: number;
    max_reversals_per_day: number;
    version: number;
  }>(() => (this.auth.orgId() ? '/v1/policy/current' : undefined));

  constructor() {
    effect(() => {
      const p = this.profile.value()?.profile;
      if (p) this.draft = { ...p };
      const policy = this.policy.value();
      if (policy) {
        this.policyDraft = {
          auto_approve_limit: policy.auto_approve_limit,
          max_refund: policy.max_refund,
          max_reversals_per_day: policy.max_reversals_per_day,
        };
      }
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

  protected async savePolicy(): Promise<void> {
    this.policyBusy.set(true);
    this.policyError.set(null);
    try {
      await firstValueFrom(this.http.put('/v1/policy/current', this.policyDraft));
      this.policy.reload();
    } catch (err) {
      this.policyError.set(errorMessage(err));
    } finally {
      this.policyBusy.set(false);
    }
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
