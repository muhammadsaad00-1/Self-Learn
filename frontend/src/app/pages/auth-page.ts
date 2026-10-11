import { Component, computed, inject, input, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';

import { Auth } from '../core/auth';
import { errorMessage } from '../core/errors';

/** Login and signup share one page; the route's `data.mode` picks which. */
@Component({
  selector: 'app-auth-page',
  imports: [ReactiveFormsModule, RouterLink],
  template: `
    <main class="auth">
      <h1>AgentFlow</h1>
      <p class="muted">Governed AI agents for financial operations</p>

      <form class="card" [formGroup]="form" (ngSubmit)="submit()">
        <h2>{{ isSignup() ? 'Create your organization' : 'Sign in' }}</h2>

        @if (isSignup()) {
          <label>Your name <input formControlName="fullName" autocomplete="name" /></label>
          <label
            >Organization <input formControlName="orgName" placeholder="EasyPay Wallet"
          /></label>
        }
        <label>Email <input formControlName="email" type="email" autocomplete="email" /></label>
        <label>
          Password
          <input
            formControlName="password"
            type="password"
            [attr.autocomplete]="isSignup() ? 'new-password' : 'current-password'"
          />
          @if (isSignup()) {
            <small class="muted">At least 12 characters. A short sentence works well.</small>
          }
        </label>

        @if (error()) {
          <p class="error" role="alert">{{ error() }}</p>
        }
        <button type="submit" [disabled]="busy()">
          {{ isSignup() ? 'Create account' : 'Sign in' }}
        </button>
      </form>

      <p class="muted">
        @if (isSignup()) {
          Already have an account? <a routerLink="/login">Sign in</a>
        } @else {
          New here? <a routerLink="/signup">Create an organization</a>
        }
      </p>
    </main>
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
      await this.router.navigateByUrl('/members');
    } catch (err) {
      this.error.set(errorMessage(err));
    } finally {
      this.busy.set(false);
    }
  }
}
