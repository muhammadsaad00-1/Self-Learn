import { Component, computed, inject, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { Router } from '@angular/router';

import { Auth } from '../core/auth';
import { errorMessage } from '../core/errors';
import { initialsOf } from '../core/format';
import { Icon } from '../layout/icon';

@Component({
  selector: 'app-profile-page',
  imports: [ReactiveFormsModule, Icon],
  template: `
    <div class="page" style="max-width: 760px">
      <header class="page-header">
        <div>
          <h1>Profile</h1>
          <p class="subtitle">One login for all your companies.</p>
        </div>
      </header>

      <section class="card" style="margin-bottom: 16px">
        <div class="card-head">
          <span class="avatar lg">{{ initials() }}</span>
          <div class="grow">
            <strong>{{ auth.me()?.user?.full_name }}</strong>
            <div class="muted">{{ auth.me()?.user?.email }}</div>
          </div>
        </div>
        <div class="table-wrap">
          <table>
            <tbody>
              @for (m of auth.me()?.memberships; track m.org_id) {
                <tr>
                  <td>
                    <div class="row" style="flex-wrap: nowrap">
                      <span class="avatar square">{{ initialsOf(m.org_name) }}</span>
                      <div>
                        <strong>{{ m.org_name }}</strong>
                        <div>
                          <small class="muted">{{ m.role }}</small>
                        </div>
                      </div>
                    </div>
                  </td>
                  <td style="text-align: right">
                    @if (m.org_id === auth.orgId()) {
                      <span class="badge" data-tone="brand">Current</span>
                    } @else {
                      <button
                        type="button"
                        class="secondary sm"
                        (click)="auth.switchCompany(m.org_id)"
                      >
                        Switch
                      </button>
                    }
                  </td>
                </tr>
              }
            </tbody>
          </table>
        </div>
      </section>

      <section class="card pad">
        <form id="add-company" class="stack" [formGroup]="form" (ngSubmit)="add()">
          <div>
            <h2>Add a company</h2>
            <p class="muted">
              A new, empty company that you own. Your login stays the same; nothing is copied from
              your other companies.
            </p>
          </div>
          <div class="row">
            <input
              class="grow"
              formControlName="orgName"
              placeholder="Company name"
              maxlength="200"
              aria-label="Company name"
            />
            <input
              class="grow"
              formControlName="password"
              type="password"
              autocomplete="current-password"
              placeholder="Confirm your password"
              aria-label="Confirm your password"
            />
            <button type="submit" [disabled]="form.invalid || busy()">
              <app-icon name="plus" /> {{ busy() ? 'Creating…' : 'Create company' }}
            </button>
          </div>
          @if (error()) {
            <div class="callout" data-tone="danger" role="alert">{{ error() }}</div>
          }
        </form>
      </section>
    </div>
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
      await this.router.navigateByUrl('/'); // now inside the new company
    } catch (err) {
      this.error.set(errorMessage(err));
      this.form.controls.password.reset(); // never leave a typed password in the form
    } finally {
      this.busy.set(false);
    }
  }
}
