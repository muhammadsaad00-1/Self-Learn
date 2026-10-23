import { HttpClient } from '@angular/common/http';
import { Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router } from '@angular/router';
import { firstValueFrom } from 'rxjs';

import type { CustomerSession as Session, LoginStartOut } from '../api/types';
import { CustomerSession } from '../core/customer-session';
import { errorMessage } from '../core/errors';
import { Icon } from '../layout/icon';

/** Customer login: mobile number, then a 6-digit one-time code. The bank comes from the
 *  link the bank gives its customers (?bank=<id>). */
@Component({
  selector: 'app-customer-login-page',
  imports: [FormsModule, Icon],
  template: `
    <div class="phone-stage">
      <div class="phone">
        <header class="phone-head">
          <div class="avatar"><app-icon name="zap" [size]="16" /></div>
          <div>
            <strong>{{ bankName() ?? 'Your bank' }}</strong
            ><small>Sign in to your account</small>
          </div>
        </header>
        <form
          class="customer-login"
          (submit)="$event.preventDefault(); step() === 'phone' ? sendCode() : verify()"
        >
          <div class="welcome">
            <span class="welcome-icon"><app-icon name="message" [size]="26" /></span>
            <h1>Your bank, by text</h1>
            <p class="muted">
              Ask anything, fix a problem, follow your issues: in English, Urdu or Roman Urdu, 24/7.
            </p>
          </div>
          @if (!bankId) {
            <p class="error" role="alert">
              This link is missing the bank. Ask your bank for its app link.
            </p>
          } @else if (step() === 'phone') {
            <label>
              Mobile number
              <input
                [(ngModel)]="phone"
                name="phone"
                inputmode="tel"
                placeholder="0300 1234567"
                required
              />
            </label>
            <button type="submit" [disabled]="busy() || phone.length < 10">Send code</button>
          } @else {
            <p class="muted">We sent a 6-digit code to {{ phone }}.</p>
            @if (devCode()) {
              <p class="fact wait">
                Demo mode (no SMS): your code is <strong>{{ devCode() }}</strong>
              </p>
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
            <button type="submit" [disabled]="busy() || code.length !== 6">Sign in</button>
            <button type="button" class="link" (click)="step.set('phone')">
              Use another number
            </button>
          }
          @if (error()) {
            <p class="error" role="alert">{{ error() }}</p>
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

  protected readonly bankId = inject(ActivatedRoute).snapshot.queryParamMap.get('bank');
  protected readonly bankName = signal<string | null>(null);
  protected readonly step = signal<'phone' | 'code'>('phone');
  protected readonly devCode = signal<string | null>(null);
  protected readonly busy = signal(false);
  protected readonly error = signal<string | null>(null);
  protected phone = '';
  protected code = '';

  constructor() {
    if (this.bankId) {
      firstValueFrom(this.http.get<{ name: string }>(`/v1/customer/bank/${this.bankId}`))
        .then((b) => this.bankName.set(b.name))
        .catch(() => this.bankName.set(null));
    }
  }

  protected async sendCode(): Promise<void> {
    await this.run(async () => {
      const out = await firstValueFrom(
        this.http.post<LoginStartOut>('/v1/customer/login/start', {
          bank_id: this.bankId,
          phone: this.phone,
        }),
      );
      this.devCode.set(out.dev_code ?? null);
      this.step.set('code');
    });
  }

  protected async verify(): Promise<void> {
    await this.run(async () => {
      const out = await firstValueFrom(
        this.http.post<Session>('/v1/customer/login/verify', {
          bank_id: this.bankId,
          phone: this.phone,
          code: this.code,
        }),
      );
      this.session.start(out.customer_token);
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
