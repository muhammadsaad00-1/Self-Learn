import { Component, computed, ElementRef, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';

import { Auth } from '../core/auth';
import { initialsOf } from '../core/format';
import { Icon } from './icon';

export { initialsOf } from '../core/format';

/**
 * The current company and user at the foot of the sidebar; opens upwards to switch company,
 * add one, open the profile or sign out (Chrome-style profiles).
 */
@Component({
  selector: 'app-profile-menu',
  imports: [RouterLink, Icon],
  host: {
    // Close when clicking anywhere outside the menu, or on Escape.
    '(document:click)': 'closeIfOutside($event)',
    '(document:keydown.escape)': 'open.set(false)',
  },
  template: `
    <button
      type="button"
      class="org-button"
      aria-haspopup="menu"
      [attr.aria-expanded]="open()"
      (click)="open.set(!open())"
    >
      <span class="avatar square">{{ initials(auth.membership()?.org_name) }}</span>
      <span class="grow">
        <strong class="truncate" style="display: block">{{ auth.membership()?.org_name }}</strong>
        <small class="muted truncate" style="display: block">{{
          auth.me()?.user?.full_name
        }}</small>
      </span>
      <app-icon name="chevron-down" />
    </button>

    @if (open()) {
      <div class="menu" role="menu" style="bottom: calc(100% + 8px); left: 0; right: 0">
        <div class="menu-user">
          <span class="avatar">{{ userInitials() }}</span>
          <div class="grow">
            <strong>{{ auth.me()?.user?.full_name }}</strong>
            <div class="muted truncate">{{ auth.me()?.user?.email }}</div>
          </div>
        </div>
        <hr />
        <div class="menu-label">Companies</div>
        @for (m of auth.me()?.memberships; track m.org_id) {
          <button
            type="button"
            class="menu-item"
            role="menuitemradio"
            [attr.aria-checked]="m.org_id === auth.orgId()"
            (click)="switchTo(m.org_id)"
          >
            <span class="avatar sm square">{{ initials(m.org_name) }}</span>
            <span class="grow">
              {{ m.org_name }}
              <small class="muted"> · {{ m.role }}</small>
            </span>
            @if (m.org_id === auth.orgId()) {
              <app-icon name="check" />
            }
          </button>
        }
        <a
          class="menu-item"
          role="menuitem"
          routerLink="/profile"
          fragment="add-company"
          (click)="open.set(false)"
        >
          <app-icon name="plus" /> Add company
        </a>
        <hr />
        <a class="menu-item" role="menuitem" routerLink="/profile" (click)="open.set(false)">
          <app-icon name="user" /> Profile
        </a>
        <button type="button" class="menu-item" role="menuitem" (click)="auth.logout()">
          <app-icon name="logout" /> Sign out
        </button>
      </div>
    }
  `,
  styles: ':host { display: block; position: relative }',
})
export class ProfileMenu {
  protected readonly auth = inject(Auth);
  private readonly host = inject<ElementRef<HTMLElement>>(ElementRef);

  protected readonly open = signal(false);
  protected readonly userInitials = computed(() => initialsOf(this.auth.me()?.user.full_name));
  protected readonly initials = initialsOf;

  protected switchTo(orgId: string): void {
    this.auth.switchCompany(orgId);
    this.open.set(false);
  }

  protected closeIfOutside(event: MouseEvent): void {
    if (!this.host.nativeElement.contains(event.target as Node)) this.open.set(false);
  }
}
