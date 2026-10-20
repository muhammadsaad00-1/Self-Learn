import { Component, computed, inject, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { Router } from '@angular/router';

import { Auth } from '../core/auth';
import { errorMessage } from '../core/errors';
import { initialsOf } from '../layout/profile-menu';

@Component({
  selector: 'app-profile-page',
  imports: [ReactiveFormsModule],
  template: `
    <h1>Profile</h1>

    <!-- Compact account strip with "Add company" directly below it. -->
    <section class="card section account">
      <div class="account-row">
        <span class="avatar large">{{ initials() }}</span>
        <div class="grow">
          <strong>{{ auth.me()?.user?.full_name }}</strong>
          <div class="muted">{{ auth.me()?.user?.email }}</div>
        </div>
        <small class="muted">One login for all your companies</small>
      </div>

      <form id="add-company" class="add-company" [formGroup]="form" (ngSubmit)="add()">
        <strong>Add company</strong>
        <input
          formControlName="orgName"
          placeholder="Company name"
          maxlength="200"
          aria-label="Company name"
        />
        <input
          formControlName="password"
          type="password"
          autocomplete="current-password"
          placeholder="Confirm your password"
          aria-label="Confirm your password"
        />
        <button type="submit" [disabled]="form.invalid || busy()">
          {{ busy() ? 'Creating…' : 'Create company' }}
        </button>
      </form>
      @if (error()) {
        <p class="error" role="alert">{{ error() }}</p>
      }
      <small class="muted">
        A new, empty company that you own. Name, email and password stay the same; nothing is copied
        from your other companies.
      </small>
    </section>

    <section class="card section">
      <h2>Your companies</h2>
      <ul class="company-list">
        @for (m of auth.me()?.memberships; track m.org_id) {
          <li>
            <span class="avatar org">{{ initialsOf(m.org_name) }}</span>
            <span class="grow">
              <strong>{{ m.org_name }}</strong> <small class="muted">{{ m.role }}</small>
            </span>
            @if (m.org_id === auth.orgId()) {
              <span class="badge">current</span>
            } @else {
              <button type="button" class="link" (click)="auth.switchCompany(m.org_id)">
                Switch
              </button>
            }
          </li>
        }
      </ul>
    </section>
  `,
})
export class ProfilePage {
  protected readonly auth = inject(Auth);
  private readonly router = inject(Router);

  protected readonly initials = computed(() => initialsOf(this.auth.me()?.user.full_name ?? ''));
  protected readonly initialsOf = initialsOf;
  protected readonly error = signal<string | null>(null);
  protected readonly busy = signal(false);
  protected readonly form = inject(FormBuilder).nonNullable.group({
    orgName: ['', Validators.required],
    password: ['', Validators.required],
  });

  protected async add(): Promise<void> {
    const { orgName, password } = this.form.getRawValue();
    this.busy.set(true);
    this.error.set(null);
    try {
      await this.auth.addCompany(orgName.trim(), password);
      this.form.reset();
      await this.router.navigateByUrl('/workflows'); // now inside the new company
    } catch (err) {
      this.error.set(errorMessage(err));
      this.form.controls.password.reset(); // never leave a typed password in the form
    } finally {
      this.busy.set(false);
    }
  }
}
