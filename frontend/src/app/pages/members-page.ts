import { DatePipe } from '@angular/common';
import { HttpClient, httpResource } from '@angular/common/http';
import { Component, computed, inject, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { firstValueFrom } from 'rxjs';

import type { Member, Role } from '../api/types';
import { Auth } from '../core/auth';
import { errorMessage } from '../core/errors';
import { FORMAT } from '../core/format';
import { Icon } from '../layout/icon';

const ALL_ROLES: Role[] = ['member', 'admin', 'owner'];

/** The team: who works in this company, and their role. */
@Component({
  selector: 'app-members-page',
  imports: [ReactiveFormsModule, DatePipe, Icon, ...FORMAT],
  template: `
    <div class="page">
      <header class="page-header">
        <div>
          <h1>Team</h1>
          <p class="subtitle">
            Everyone here can work the inbox and tickets. Admins also approve refunds and see the
            audit log.
          </p>
        </div>
      </header>

      @if (auth.isAdmin()) {
        <form
          class="card pad row"
          style="margin-bottom: 16px"
          [formGroup]="form"
          (ngSubmit)="add()"
        >
          <span class="avatar"><app-icon name="plus" /></span>
          <input
            class="grow"
            formControlName="email"
            type="email"
            placeholder="colleague@company.com"
            aria-label="Email"
          />
          <select formControlName="role" aria-label="Role">
            @for (r of assignableRoles(); track r) {
              <option [value]="r">{{ r | human }}</option>
            }
          </select>
          <button type="submit" [disabled]="form.invalid">Add to team</button>
          <small class="muted" style="flex-basis: 100%; padding-left: 42px">
            They need an AgentFlow account first; email invitations come later.
          </small>
        </form>
      }

      @if (error()) {
        <div class="callout" data-tone="danger" role="alert" style="margin-bottom: 16px">
          {{ error() }}
        </div>
      }

      <section class="card">
        @if (members.isLoading()) {
          <div class="card-body stack-sm">
            <div class="skeleton"></div>
            <div class="skeleton" style="width: 60%"></div>
          </div>
        } @else if (members.error()) {
          <div class="card-body">
            <div class="callout" data-tone="danger">{{ message(members.error()) }}</div>
          </div>
        } @else {
          <div class="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Role</th>
                  <th>Joined</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                @for (m of members.value(); track m.user_id) {
                  <tr>
                    <td>
                      <div class="row" style="flex-wrap: nowrap">
                        <span class="avatar">{{ m.full_name | initials }}</span>
                        <div>
                          <strong>{{ m.full_name }}</strong>
                          @if (m.user_id === auth.me()?.user?.id) {
                            <span class="badge" style="margin-left: 6px">You</span>
                          }
                          <div>
                            <small class="muted">{{ m.email }}</small>
                          </div>
                        </div>
                      </div>
                    </td>
                    <td>
                      @if (canManage(m)) {
                        <select
                          (change)="changeRole(m, $any($event.target).value)"
                          aria-label="Role"
                        >
                          @for (r of assignableRoles(); track r) {
                            <option [value]="r" [selected]="r === m.role">{{ r | human }}</option>
                          }
                        </select>
                      } @else {
                        <span
                          class="badge"
                          [attr.data-tone]="m.role === 'owner' ? 'brand' : 'neutral'"
                          >{{ m.role | human }}</span
                        >
                      }
                    </td>
                    <td>
                      <span class="muted">{{ m.joined_at | date: 'mediumDate' }}</span>
                    </td>
                    <td style="text-align: right">
                      @if (canManage(m)) {
                        <button class="ghost sm" (click)="remove(m)">Remove</button>
                      }
                    </td>
                  </tr>
                }
              </tbody>
            </table>
          </div>
        }
      </section>
    </div>
  `,
})
export class MembersPage {
  protected readonly auth = inject(Auth);
  private readonly http = inject(HttpClient);

  // Re-fetches automatically whenever the selected org changes: this request function reads
  // the auth.orgId() signal, so a new org produces a new request.
  protected readonly members = httpResource<Member[]>(() => {
    const orgId = this.auth.orgId();
    return orgId ? { url: '/v1/members', headers: { 'X-Org-Id': orgId } } : undefined;
  });

  protected readonly error = signal<string | null>(null);
  protected readonly message = errorMessage;

  // UI mirror of the server's rules (service.check_can_manage). The server enforces them
  // regardless; this only avoids showing controls that would fail.
  protected readonly assignableRoles = computed(() =>
    this.auth.role() === 'owner' ? ALL_ROLES : ALL_ROLES.filter((r) => r !== 'owner'),
  );
  /** Admins manage others (owners only by owners); never your own row, so nobody demotes or
   *  removes themselves by accident. */
  protected canManage(m: Member): boolean {
    if (m.user_id === this.auth.me()?.user.id) return false;
    return this.auth.isAdmin() && (m.role !== 'owner' || this.auth.role() === 'owner');
  }

  protected readonly form = inject(FormBuilder).nonNullable.group({
    email: ['', [Validators.required, Validators.email]],
    role: ['member' as Role],
  });

  protected add(): Promise<void> {
    return this.run(async () => {
      await firstValueFrom(this.http.post('/v1/members', this.form.getRawValue()));
      this.form.reset();
    });
  }

  protected changeRole(m: Member, role: Role): Promise<void> {
    return this.run(() => firstValueFrom(this.http.patch(`/v1/members/${m.user_id}`, { role })));
  }

  protected remove(m: Member): Promise<void> {
    return this.run(() => firstValueFrom(this.http.delete(`/v1/members/${m.user_id}`)));
  }

  /** Run a change, show any API error, then reload the list (also reverts a refused edit). */
  private async run(change: () => Promise<unknown>): Promise<void> {
    this.error.set(null);
    try {
      await change();
    } catch (err) {
      this.error.set(errorMessage(err));
    }
    this.members.reload();
    await this.auth.loadMe(); // my own role may have changed
  }
}
