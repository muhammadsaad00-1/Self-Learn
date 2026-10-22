import { HttpClient } from '@angular/common/http';
import { Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router } from '@angular/router';
import { firstValueFrom } from 'rxjs';

import type { CustomerSession as Session, LoginStartOut } from '../api/types';
import { CustomerSession } from '../core/customer-session';
import { errorMessage } from '../core/errors';

/** Customer login: mobile number, then a 6-digit one-time code. The bank comes from the
 *  link the bank gives its customers (?bank=<id>). */
@Component({
  selector: 'app-customer-login-page',
  imports: [FormsModule],
  template: `
    <div class="phone">
      <header class="phone-head">
        <div class="avatar">AF</div>
        <div><strong>AgentFlow Bank</strong><small>Sign in to your account</small></div>
      </header>
      <form
        class="customer-login"
        (submit)="$event.preventDefault(); step() === 'phone' ? sendCode() : verify()"
      >
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
          <button type="button" class="link" (click)="step.set('phone')">Use another number</button>
        }
        @if (error()) {
          <p class="error" role="alert">{{ error() }}</p>
        }
      </form>
    </div>
  `,
})
export class CustomerLoginPage {
  private readonly http = inject(HttpClient);
  private readonly session = inject(CustomerSession);
  private readonly router = inject(Router);

  protected readonly bankId = inject(ActivatedRoute).snapshot.queryParamMap.get('bank');
  protected readonly step = signal<'phone' | 'code'>('phone');
  protected readonly devCode = signal<string | null>(null);
  protected readonly busy = signal(false);
  protected readonly error = signal<string | null>(null);
  protected phone = '';
  protected code = '';

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
