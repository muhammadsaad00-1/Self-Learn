import { Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';

import type { BusinessProfile, Industry } from '../api/types';
import { Auth } from '../core/auth';
import { errorMessage } from '../core/errors';
import { Icon } from '../layout/icon';

type Step = 0 | 1 | 2;
type Channel = BusinessProfile['channels'][number];
type Language = BusinessProfile['languages'][number];

/** Who AgentFlow is for: companies that move customers' money (see backend `Industry`). */
const INDUSTRIES: { id: Industry; label: string; hint: string; icon: string }[] = [
  {
    id: 'commercial_bank',
    label: 'Commercial bank',
    hint: 'Accounts, cards, transfers',
    icon: 'building',
  },
  {
    id: 'microfinance_bank',
    label: 'Microfinance bank',
    hint: 'Small loans and savings',
    icon: 'users',
  },
  { id: 'digital_wallet', label: 'Wallet or payments', hint: 'Mobile wallets, PSPs', icon: 'zap' },
  { id: 'digital_lender', label: 'Digital lender', hint: 'App-based lending', icon: 'money' },
];

const CHANNELS: { id: Channel; label: string }[] = [
  { id: 'app_chat', label: 'In-app chat' },
  { id: 'email', label: 'Email' },
  { id: 'whatsapp', label: 'WhatsApp' },
  { id: 'phone', label: 'Phone' },
  { id: 'branch', label: 'Branch' },
];

const LANGUAGES: { id: Language; label: string }[] = [
  { id: 'en', label: 'English' },
  { id: 'ur', label: 'Urdu' },
  { id: 'roman_ur', label: 'Roman Urdu' },
];

const VOLUMES = [1000, 5000, 20000, 100000];

/**
 * Create a company in three short steps: you, your business, your support today. The
 * business answers size the ROI the Overview shows and tailor the setup checklist.
 */
@Component({
  selector: 'app-signup-wizard',
  imports: [FormsModule, RouterLink, Icon],
  template: `
    <div class="auth-split">
      <section class="auth-hero">
        <div class="brand">
          <span class="brand-mark"><app-icon name="zap" [size]="16" /></span>
          AgentFlow
        </div>
        <div>
          <h1>Set up your AI support desk in two minutes.</h1>
          <div class="points">
            @for (s of steps; track s; let i = $index) {
              <div [style.opacity]="i <= step() ? 1 : 0.55">
                <app-icon [name]="i < step() ? 'check-circle' : 'clock'" [size]="18" />
                <span>{{ s }}</span>
              </div>
            }
          </div>
        </div>
        <small style="color: rgb(236 253 245 / 0.6)">Built for companies that move money.</small>
      </section>

      <section class="auth-panel">
        <form
          class="auth-form"
          style="width: min(460px, 100%)"
          (submit)="$event.preventDefault(); next()"
        >
          <div class="stepper">
            @for (s of steps; track s; let i = $index) {
              <span [class.done]="i < step()" [class.current]="i === step()"></span>
            }
          </div>

          @switch (step()) {
            @case (0) {
              <div class="stack-sm">
                <h1>Create your workspace</h1>
                <p class="muted">First, you. You'll be the owner and can invite your team later.</p>
              </div>
              <label
                >Your name
                <input
                  [(ngModel)]="fullName"
                  name="fullName"
                  autocomplete="name"
                  placeholder="Ayesha Khan"
              /></label>
              <label
                >Work email
                <input
                  [(ngModel)]="email"
                  name="email"
                  type="email"
                  autocomplete="email"
                  placeholder="you@company.com"
              /></label>
              <label>
                Password
                <input
                  [(ngModel)]="password"
                  name="password"
                  type="password"
                  autocomplete="new-password"
                />
                <span class="field-hint">At least 12 characters. A short sentence works well.</span>
              </label>
            }
            @case (1) {
              <div class="stack-sm">
                <h1>Your business</h1>
                <p class="muted">
                  AgentFlow resolves money problems: reversals, disputes, frozen cards. Which are
                  you?
                </p>
              </div>
              <label
                >Company name
                <input [(ngModel)]="orgName" name="orgName" placeholder="Mehran Microfinance Bank"
              /></label>
              <div class="choice-grid" role="radiogroup" aria-label="Industry">
                @for (i of industries; track i.id) {
                  <button
                    type="button"
                    class="choice"
                    role="radio"
                    [attr.aria-checked]="industry === i.id"
                    [class.selected]="industry === i.id"
                    (click)="industry = i.id"
                  >
                    <app-icon [name]="i.icon" [size]="18" />
                    <strong>{{ i.label }}</strong>
                    <small class="muted">{{ i.hint }}</small>
                  </button>
                }
              </div>
            }
            @case (2) {
              <div class="stack-sm">
                <h1>Your support today</h1>
                <p class="muted">
                  So we can show what AgentFlow saves you. Rough numbers are fine.
                </p>
              </div>
              <div class="stack-sm">
                <span class="label">Customer messages a month</span>
                <div class="segmented">
                  @for (v of volumes; track v) {
                    <button
                      type="button"
                      [class.active]="monthlyQueries === v"
                      (click)="monthlyQueries = v"
                    >
                      {{ v >= 100000 ? '100k+' : v / 1000 + 'k' }}
                    </button>
                  }
                </div>
              </div>
              <div class="stack-sm">
                <span class="label">Where customers reach you</span>
                <div class="row">
                  @for (c of channelOptions; track c.id) {
                    <button
                      type="button"
                      class="toggle"
                      [class.on]="channels.has(c.id)"
                      (click)="toggle(channels, c.id)"
                    >
                      {{ c.label }}
                    </button>
                  }
                </div>
              </div>
              <div class="stack-sm">
                <span class="label">Languages they write in</span>
                <div class="row">
                  @for (l of languageOptions; track l.id) {
                    <button
                      type="button"
                      class="toggle"
                      [class.on]="languages.has(l.id)"
                      (click)="toggle(languages, l.id)"
                    >
                      {{ l.label }}
                    </button>
                  }
                </div>
              </div>
              <div class="row" style="flex-wrap: nowrap">
                <label class="grow"
                  >Minutes per query
                  <input [(ngModel)]="minutesPerQuery" name="mpq" type="number" min="1" max="240"
                /></label>
                <label class="grow"
                  >Agent cost / hour (PKR)
                  <input [(ngModel)]="costPerHour" name="cph" type="number" min="0"
                /></label>
              </div>
            }
          }

          @if (error()) {
            <div class="callout" data-tone="danger" role="alert">{{ error() }}</div>
          }
          <div class="row">
            @if (step() > 0) {
              <button type="button" class="secondary" (click)="back()">Back</button>
            }
            <span class="spacer"></span>
            <button type="submit" [disabled]="busy()">
              {{ step() < 2 ? 'Continue' : busy() ? 'Creating…' : 'Create workspace' }}
              <app-icon name="chevron-left" style="transform: rotate(180deg)" />
            </button>
          </div>
          <p class="muted" style="text-align: center">
            Already have an account? <a routerLink="/login">Sign in</a>
          </p>
        </form>
      </section>
    </div>
  `,
})
export class SignupWizard {
  private readonly auth = inject(Auth);
  private readonly router = inject(Router);

  protected readonly steps = ['About you', 'Your business', 'Your support today'];
  protected readonly industries = INDUSTRIES;
  protected readonly channelOptions = CHANNELS;
  protected readonly languageOptions = LANGUAGES;
  protected readonly volumes = VOLUMES;
  protected readonly step = signal<Step>(0);
  protected readonly busy = signal(false);
  protected readonly error = signal<string | null>(null);

  protected fullName = '';
  protected email = '';
  protected password = '';
  protected orgName = '';
  protected industry: Industry | null = null;
  protected monthlyQueries = 5000;
  protected channels = new Set<Channel>(['app_chat', 'email']);
  protected languages = new Set<Language>(['en', 'roman_ur']);
  protected minutesPerQuery = 8;
  protected costPerHour = 1500;

  protected toggle<T>(set: Set<T>, value: T): void {
    if (set.has(value)) set.delete(value);
    else set.add(value);
  }

  protected back(): void {
    this.error.set(null);
    this.step.set((this.step() - 1) as Step);
  }

  /** Validate the current step; submit on the last one. */
  protected async next(): Promise<void> {
    const problem = this.check();
    this.error.set(problem);
    if (problem) return;
    if (this.step() < 2) {
      this.step.set((this.step() + 1) as Step);
      return;
    }
    this.busy.set(true);
    try {
      await this.auth.signup({
        full_name: this.fullName.trim(),
        email: this.email.trim(),
        password: this.password,
        org_name: this.orgName.trim(),
        profile: {
          industry: this.industry!,
          country: 'PK',
          currency: 'PKR',
          // ponytail: a rough size from the message volume; ask explicitly if it ever matters
          customers:
            this.monthlyQueries >= 100000
              ? 'over_1m'
              : this.monthlyQueries >= 20000
                ? '100k_1m'
                : '10k_100k',
          monthly_queries: this.monthlyQueries,
          channels: [...this.channels],
          languages: [...this.languages],
          minutes_per_query: this.minutesPerQuery,
          agent_cost_per_hour: this.costPerHour,
        },
      });
      await this.router.navigateByUrl('/');
    } catch (err) {
      this.error.set(errorMessage(err));
    } finally {
      this.busy.set(false);
    }
  }

  private check(): string | null {
    switch (this.step()) {
      case 0:
        if (!this.fullName.trim() || !/^\S+@\S+\.\S+$/.test(this.email.trim())) {
          return 'Please enter your name and a valid work email.';
        }
        return this.password.length < 12 ? 'The password needs at least 12 characters.' : null;
      case 1:
        if (!this.orgName.trim()) return 'Please enter your company name.';
        return this.industry ? null : 'Please choose what kind of business you are.';
      default:
        if (!this.channels.size || !this.languages.size) {
          return 'Pick at least one channel and one language.';
        }
        return null;
    }
  }
}
