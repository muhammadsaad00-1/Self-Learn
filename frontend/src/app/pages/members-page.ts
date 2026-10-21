import { DatePipe } from '@angular/common';
import { HttpClient, httpResource } from '@angular/common/http';
import { Component, computed, inject, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { firstValueFrom } from 'rxjs';

import type { Member, Role } from '../api/types';
import { Auth } from '../core/auth';
import { errorMessage } from '../core/errors';

const ALL_ROLES: Role[] = ['member', 'admin', 'owner'];

@Component({
  selector: 'app-members-page',
  imports: [ReactiveFormsModule, DatePipe],
  template: `
    <h1>Members</h1>

    @if (auth.isAdmin()) {
      <form class="card row" [formGroup]="form" (ngSubmit)="add()">
        <input formControlName="email" type="email" placeholder="colleague@company.com" />
        <select formControlName="role">
          @for (r of assignableRoles(); track r) {
            <option [value]="r">{{ r }}</option>
          }
        </select>
        <button type="submit" [disabled]="form.invalid">Add member</button>
      </form>
      <p class="muted">They need an AgentFlow account first. Email invitations come later.</p>
    }

    @if (error()) {
      <p class="error" role="alert">{{ error() }}</p>
    }

    @if (members.isLoading()) {
      <p class="muted">Loading…</p>
    } @else if (members.error()) {
      <p class="error">{{ message(members.error()) }}</p>
    } @else {
      <table>
        <thead>
          <tr>
            <th>Name</th>
            <th>Email</th>
            <th>Role</th>
            <th>Joined</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          @for (m of members.value(); track m.user_id) {
            <tr>
              <td>{{ m.full_name }}</td>
              <td>{{ m.email }}</td>
              <td>
                @if (canManage(m)) {
                  <select (change)="changeRole(m, $any($event.target).value)">
                    @for (r of assignableRoles(); track r) {
                      <option [value]="r" [selected]="r === m.role">{{ r }}</option>
                    }
                  </select>
                } @else {
                  {{ m.role }}
                }
              </td>
              <td>{{ m.joined_at | date: 'mediumDate' }}</td>
              <td>
                @if (canManage(m)) {
                  <button class="link danger" (click)="remove(m)">Remove</button>
                }
              </td>
            </tr>
          }
        </tbody>
      </table>
    }
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
  protected canManage(m: Member): boolean {
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
