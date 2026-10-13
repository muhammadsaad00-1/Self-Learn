import { Component, inject } from '@angular/core';
import { Router, RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';

import { Auth } from '../core/auth';

/** Frame around every logged-in page: org switcher, navigation, user menu. */
@Component({
  selector: 'app-shell',
  imports: [RouterOutlet, RouterLink, RouterLinkActive],
  template: `
    <header class="topbar">
      <strong>AgentFlow</strong>

      <label class="org-switcher">
        <span class="sr-only">Organization</span>
        <!-- [selected] on the option, not [value] on the select: the select's value would be
             applied before @for has rendered the options, and the browser would ignore it. -->
        <select (change)="switchOrg($any($event.target).value)">
          @for (m of auth.me()?.memberships; track m.org_id) {
            <option [value]="m.org_id" [selected]="m.org_id === auth.orgId()">
              {{ m.org_name }} · {{ m.role }}
            </option>
          }
        </select>
      </label>

      <nav>
        <a routerLink="/workflows" routerLinkActive="active">Workflows</a>
        <a routerLink="/members" routerLinkActive="active">Members</a>
        @if (auth.isAdmin()) {
          <a routerLink="/audit" routerLinkActive="active">Audit log</a>
        }
      </nav>

      <span class="spacer"></span>
      <span class="muted">{{ auth.me()?.user?.full_name }}</span>
      <button class="link" (click)="auth.logout()">Sign out</button>
    </header>

    <main class="page"><router-outlet /></main>
  `,
})
export class Shell {
  protected readonly auth = inject(Auth);
  private readonly router = inject(Router);

  protected switchOrg(orgId: string): void {
    this.auth.selectOrg(orgId);
    // Pages reload their data on their own (they react to auth.orgId()). Only the audit
    // page needs a redirect, when the new org's role isn't allowed to see it.
    if (this.router.url.startsWith('/audit') && !this.auth.isAdmin()) {
      void this.router.navigateByUrl('/members');
    }
  }
}
