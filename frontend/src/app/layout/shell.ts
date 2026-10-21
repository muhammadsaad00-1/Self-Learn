import { Component, inject } from '@angular/core';
import { RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';

import { Auth } from '../core/auth';
import { ProfileMenu } from './profile-menu';

/** Frame around every logged-in page: navigation and the profile (company) menu. */
@Component({
  selector: 'app-shell',
  imports: [RouterOutlet, RouterLink, RouterLinkActive, ProfileMenu],
  template: `
    <header class="topbar">
      <strong>AgentFlow</strong>

      <nav>
        <a routerLink="/insights" routerLinkActive="active">Insights</a>
        <a routerLink="/chats" routerLinkActive="active">Chats</a>
        <a routerLink="/workflows" routerLinkActive="active">Workflows</a>
        <a routerLink="/tickets" routerLinkActive="active">Tickets</a>
        <a routerLink="/knowledge" routerLinkActive="active">Knowledge</a>
        <a routerLink="/members" routerLinkActive="active">Members</a>
        @if (auth.isAdmin()) {
          <a routerLink="/approvals" routerLinkActive="active">Approvals</a>
          <a routerLink="/audit" routerLinkActive="active">Audit log</a>
        }
      </nav>

      <span class="spacer"></span>
      <app-profile-menu />
    </header>

    <main class="page"><router-outlet /></main>
  `,
})
export class Shell {
  protected readonly auth = inject(Auth);
}
