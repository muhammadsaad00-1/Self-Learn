import { HttpClient } from '@angular/common/http';
import { Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router } from '@angular/router';
import { firstValueFrom } from 'rxjs';

import type { BankOption, CustomerSession as Session, LoginStartOut } from '../api/types';
import { CustomerSession } from '../core/customer-session';
import { errorMessage } from '../core/errors';
import { Icon } from '../layout/icon';

/** Customer login: mobile number, then a 6-digit one-time code. The bank comes from the
 *  link the bank gives its customers (?bank=<id>). */
/** Customer sign-in: mobile number + username (given by the bank) -> which bank, if they
 *  bank with several -> 6-digit one-time code -> that bank's customer app. A bank's own app
 *  link (?bank=<id>) preselects the bank. Never leads into the team's dashboard. */
@Component({
  selector: 'app-customer-login-page',
  imports: [FormsModule, Icon],
  template: `
    <div class="phone-stage">
      <div class="phone">
        <header class="phone-head">
          <div class="avatar"><app-icon name="zap" [size]="16" /></div>
          <div>
            <strong>{{ bank()?.name ?? 'Customer sign in' }}</strong>
            <small>{{ bank() ? 'Sign in to your account' : 'Your bank, by text' }}</small>
          </div>
        </header>
        <form class="customer-login" (submit)="$event.preventDefault(); submit()">
          @switch (step()) {
            @case ('details') {
              <div class="welcome">
                <span class="welcome-icon"><app-icon name="message" [size]="26" /></span>
                <h1>Your bank, by text</h1>
                <p class="muted">
                  Ask anything, fix a problem, follow your issues: in English, Urdu or Roman Urdu,
                  24/7.
                </p>
              </div>
              <label>
                Mobile number
                <input
                  [(ngModel)]="phone"
                  name="phone"
                  inputmode="tel"
                  autocomplete="tel"
                  placeholder="0300 1234567"
                  required
                />
              </label>
              <label>
                Username
                <input
                  [(ngModel)]="username"
                  name="username"
                  autocomplete="username"
                  autocapitalize="none"
                  placeholder="e.g. usman.tariq"
                  required
                />
                <span class="field-hint">The username your bank gave you.</span>
              </label>
              <button
                type="submit"
                [disabled]="busy() || phone.trim().length < 10 || username.trim().length < 2"
              >
                {{ busy() ? 'Checking…' : 'Send code' }}
              </button>
            }
            @case ('bank') {
              <div class="stack-sm">
                <h2>Which bank?</h2>
                <p class="muted">You have accounts with more than one. Choose where to sign in.</p>
              </div>
              <div class="stack-sm">
                @for (b of banks(); track b.id) {
                  <button type="button" class="choice" (click)="chooseBank(b)" [disabled]="busy()">
                    <app-icon name="building" [size]="18" />
                    <strong>{{ b.name }}</strong>
                  </button>
                }
              </div>
              <button type="button" class="link" (click)="restart()">Use other details</button>
            }
            @case ('code') {
              <div class="stack-sm">
                <h2>Enter your code</h2>
                <p class="muted">
                  If these details match an account{{ bank() ? ' at ' + bank()!.name : '' }}, we
                  sent a 6-digit code to {{ phone }}.
                </p>
              </div>
              @if (devCode()) {
                <div class="fact wait">
                  Demo mode (no SMS): your code is <strong>{{ devCode() }}</strong>
                </div>
              }
              <label>
                Code
                <input
                  [(ngModel)]="code"
                  name="code"
                  inputmode="numeric"
                  maxlength="6"
                  autocomplete="one-time-code"
                  required
                />
              </label>
              <button type="submit" [disabled]="busy() || code.length !== 6">
                {{ busy() ? 'Signing in…' : 'Sign in' }}
              </button>
              <button type="button" class="link" (click)="restart()">Use other details</button>
            }
          }
          @if (error()) {
            <div class="callout" data-tone="danger" role="alert">{{ error() }}</div>
          }
          <div class="trust">
            <app-icon name="shield" [size]="16" />
            <span
              >We text you a one-time code. The bank never asks for your PIN, CVV or OTP in chat or
              on a call.</span
            >
          </div>
        </form>
      </div>
    </div>
  `,
})
export class CustomerLoginPage {
  private readonly http = inject(HttpClient);
  private readonly session = inject(CustomerSession);
  private readonly router = inject(Router);

  /** From the bank's own app link, if the customer came that way. */
  private readonly linkBankId = inject(ActivatedRoute).snapshot.queryParamMap.get('bank');
  protected readonly bank = signal<BankOption | null>(null);
  protected readonly banks = signal<BankOption[]>([]);
  protected readonly step = signal<'details' | 'bank' | 'code'>('details');
  protected readonly devCode = signal<string | null>(null);
  protected readonly busy = signal(false);
  protected readonly error = signal<string | null>(null);
  protected phone = '';
  protected username = '';
  protected code = '';

  constructor() {
    if (this.linkBankId) {
      firstValueFrom(this.http.get<{ name: string }>(`/v1/customer/bank/${this.linkBankId}`))
        .then((b) => this.bank.set({ id: this.linkBankId!, name: b.name }))
        .catch(() => undefined); // an unknown bank in the link: sign in without it
    }
  }

  protected submit(): Promise<void> {
    return this.step() === 'code' ? this.verify() : this.start(this.bank()?.id ?? null);
  }

  protected chooseBank(b: BankOption): Promise<void> {
    return this.start(b.id);
  }

  protected restart(): void {
    this.step.set('details');
    this.code = '';
    this.devCode.set(null);
    this.error.set(null);
    if (!this.linkBankId) this.bank.set(null);
  }

  private async start(bankId: string | null): Promise<void> {
    await this.run(async () => {
      const out = await firstValueFrom(
        this.http.post<LoginStartOut>('/v1/customer/login/start', {
          phone: this.phone,
          username: this.username,
          bank_id: bankId,
        }),
      );
      if (!out.sent) {
        this.banks.set(out.banks);
        this.step.set('bank');
        return;
      }
      if (out.bank) this.bank.set(out.bank);
      this.devCode.set(out.dev_code ?? null);
      this.step.set('code');
    });
  }

  private async verify(): Promise<void> {
    const bank = this.bank();
    if (!bank) {
      this.error.set('Wrong or expired code');
      return;
    }
    await this.run(async () => {
      const out = await firstValueFrom(
        this.http.post<Session>('/v1/customer/login/verify', {
          bank_id: bank.id,
          phone: this.phone,
          code: this.code,
        }),
      );
      this.session.start(out.customer_token, bank.name);
      await this.router.navigateByUrl('/customer');
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
