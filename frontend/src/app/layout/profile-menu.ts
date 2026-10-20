import { Component, computed, ElementRef, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';

import { Auth } from '../core/auth';

/** "Sara Khan" -> "SK"; used for the round avatar badges. */
export function initialsOf(name: string): string {
  const words = name.split(/\s+/).filter(Boolean).slice(0, 2);
  return words.map((w) => w[0]!.toUpperCase()).join('') || '?';
}

/**
 * Chrome-style profile menu: the current company on a button; clicking it lists all of the
 * user's companies to switch between, plus "Add company", "Profile" and "Sign out".
 */
@Component({
  selector: 'app-profile-menu',
  imports: [RouterLink],
  host: {
    // Close when clicking anywhere outside the menu, or on Escape.
    '(document:click)': 'closeIfOutside($event)',
    '(document:keydown.escape)': 'open.set(false)',
  },
  template: `
    <button
      type="button"
      class="profile-button"
      aria-haspopup="menu"
      [attr.aria-expanded]="open()"
      (click)="open.set(!open())"
    >
      <span class="avatar">{{ userInitials() }}</span>
      <span>{{ auth.membership()?.org_name }}</span>
      <span aria-hidden="true">▾</span>
    </button>

    @if (open()) {
      <div class="menu card" role="menu">
        <div class="menu-user">
          <span class="avatar large">{{ userInitials() }}</span>
          <div>
            <strong>{{ auth.me()?.user?.full_name }}</strong>
            <div class="muted">{{ auth.me()?.user?.email }}</div>
          </div>
        </div>

        <div class="menu-label">Companies</div>
        @for (m of auth.me()?.memberships; track m.org_id) {
          <button
            type="button"
            class="menu-item"
            role="menuitemradio"
            [attr.aria-checked]="m.org_id === auth.orgId()"
            (click)="switchTo(m.org_id)"
          >
            <span class="avatar org">{{ initials(m.org_name) }}</span>
            <span class="grow">
              {{ m.org_name }}
              <small class="muted">{{ m.role }}</small>
            </span>
            @if (m.org_id === auth.orgId()) {
              <span aria-label="current company">✓</span>
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
          + Add company
        </a>

        <hr />
        <a class="menu-item" role="menuitem" routerLink="/profile" (click)="open.set(false)"
          >Profile</a
        >
        <button type="button" class="menu-item" role="menuitem" (click)="auth.logout()">
          Sign out
        </button>
      </div>
    }
  `,
})
export class ProfileMenu {
  protected readonly auth = inject(Auth);
  private readonly host = inject<ElementRef<HTMLElement>>(ElementRef);

  protected readonly open = signal(false);
  protected readonly userInitials = computed(() =>
    initialsOf(this.auth.me()?.user.full_name ?? ''),
  );
  protected readonly initials = initialsOf;

  protected switchTo(orgId: string): void {
    this.auth.switchCompany(orgId);
    this.open.set(false);
  }

  protected closeIfOutside(event: MouseEvent): void {
    if (!this.host.nativeElement.contains(event.target as Node)) this.open.set(false);
  }
}
