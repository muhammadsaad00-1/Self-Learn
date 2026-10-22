import { Component, computed, inject, input, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';

import { Auth } from '../core/auth';
import { errorMessage } from '../core/errors';
import { Icon } from '../layout/icon';

/** Login and signup share one page; the route's `data.mode` picks which. */
@Component({
  selector: 'app-auth-page',
  imports: [ReactiveFormsModule, RouterLink, Icon],
  template: `
    <div class="auth-split">
      <section class="auth-hero">
        <div class="brand">
          <span class="brand-mark"><app-icon name="zap" [size]="16" /></span>
          AgentFlow
        </div>
        <div>
          <h1>Customers text. AI resolves. Your team decides what matters.</h1>
          <div class="points">
            <div>
              <app-icon name="message" [size]="18" />
              <span
                >Answers from your own documents, in English, Urdu or Roman Urdu: typed or voice
                notes.</span
              >
            </div>
            <div>
              <app-icon name="zap" [size]="18" />
              <span
                >Failed transfers reversed, duplicates refunded, cards frozen on fraud: in
                seconds.</span
              >
            </div>
            <div>
              <app-icon name="shield" [size]="18" />
              <span
                >Policy in code, maker-checker approvals, exactly-once money movement, full audit
                trail.</span
              >
            </div>
          </div>
        </div>
        <small style="color: rgb(236 253 245 / 0.6)"
          >Governed AI agents for banks and microfinance</small
        >
      </section>

      <section class="auth-panel">
        <form class="auth-form" [formGroup]="form" (ngSubmit)="submit()">
          <div class="stack-sm">
            <h1>{{ isSignup() ? 'Create your company' : 'Welcome back' }}</h1>
            <p class="muted">
              {{
                isSignup()
                  ? 'Set up AgentFlow for your team in a minute.'
                  : 'Sign in to your team workspace.'
              }}
            </p>
          </div>

          @if (isSignup()) {
            <label
              >Your name
              <input formControlName="fullName" autocomplete="name" placeholder="Ayesha Khan"
            /></label>
            <label
              >Company <input formControlName="orgName" placeholder="Mehran Microfinance Bank"
            /></label>
          }
          <label
            >Work email
            <input
              formControlName="email"
              type="email"
              autocomplete="email"
              placeholder="you@company.com"
          /></label>
          <label>
            Password
            <input
              formControlName="password"
              type="password"
              [attr.autocomplete]="isSignup() ? 'new-password' : 'current-password'"
            />
            @if (isSignup()) {
              <span class="field-hint">At least 12 characters. A short sentence works well.</span>
            }
          </label>

          @if (error()) {
            <div class="callout" data-tone="danger" role="alert">{{ error() }}</div>
          }
          <button type="submit" [disabled]="busy()">
            {{ busy() ? 'One moment…' : isSignup() ? 'Create company' : 'Sign in' }}
          </button>

          <p class="muted" style="text-align: center">
            @if (isSignup()) {
              Already have an account? <a routerLink="/login">Sign in</a>
            } @else {
              New here? <a routerLink="/signup">Create a company</a>
            }
          </p>
        </form>
      </section>
    </div>
  `,
})
export class AuthPage {
  private readonly auth = inject(Auth);
  private readonly router = inject(Router);

  readonly mode = input<'login' | 'signup'>('login'); // from route data
  protected readonly isSignup = computed(() => this.mode() === 'signup');
  protected readonly error = signal<string | null>(null);
  protected readonly busy = signal(false);

  // Signup-only fields are validated in submit(), so the login form isn't blocked by them.
  protected readonly form = inject(FormBuilder).nonNullable.group({
    fullName: [''],
    orgName: [''],
    email: ['', [Validators.required, Validators.email]],
    password: ['', Validators.required],
  });

  protected async submit(): Promise<void> {
    const { fullName, orgName, email, password } = this.form.getRawValue();
    if (this.form.invalid || (this.isSignup() && (!fullName || !orgName))) {
      this.error.set('Please fill in every field.');
      return;
    }
    this.busy.set(true);
    this.error.set(null);
    try {
      if (this.isSignup()) {
        await this.auth.signup({ email, password, full_name: fullName, org_name: orgName });
      } else {
        await this.auth.login(email, password);
      }
      await this.router.navigateByUrl('/');
    } catch (err) {
      this.error.set(errorMessage(err));
    } finally {
      this.busy.set(false);
    }
  }
}
