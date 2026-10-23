import { httpResource } from '@angular/common/http';
import { Component, DestroyRef, computed, inject } from '@angular/core';
import { RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';

import type { Approval, Conversation, Ticket } from '../api/types';
import { Auth } from '../core/auth';
import { Icon } from './icon';
import { ProfileMenu } from './profile-menu';

const COUNTS_POLL_MS = 15000;

/** Frame around every staff page: grouped navigation with what's waiting, and the company
 *  menu at the foot. */
@Component({
  selector: 'app-shell',
  imports: [RouterOutlet, RouterLink, RouterLinkActive, ProfileMenu, Icon],
  template: `
    <div class="app">
      <aside class="sidebar">
        <div class="brand">
          <span class="brand-mark"><app-icon name="zap" [size]="16" /></span>
          AgentFlow
        </div>

        <a class="nav-link" routerLink="/insights" routerLinkActive="active">
          <app-icon name="chart" /> Overview
        </a>

        <div class="nav-group">Customers</div>
        <a class="nav-link" routerLink="/chats" routerLinkActive="active">
          <app-icon name="inbox" /> Inbox
          @if (openIssues()) {
            <span class="count calm" title="Open issues">{{ openIssues() }}</span>
          }
        </a>
        <a class="nav-link" routerLink="/tickets" routerLinkActive="active">
          <app-icon name="ticket" /> Tickets
          @if (openTickets()) {
            <span class="count" title="Open tickets">{{ openTickets() }}</span>
          }
        </a>
        @if (auth.isAdmin()) {
          <a class="nav-link" routerLink="/approvals" routerLinkActive="active">
            <app-icon name="check-circle" /> Approvals
            @if (pendingApprovals()) {
              <span class="count" title="Waiting for a decision">{{ pendingApprovals() }}</span>
            }
          </a>
        }

        <div class="nav-group">Operations</div>
        <a class="nav-link" routerLink="/workflows" routerLinkActive="active">
          <app-icon name="activity" /> Requests
        </a>
        <a class="nav-link" routerLink="/knowledge" routerLinkActive="active">
          <app-icon name="book" /> Knowledge
        </a>

        <div class="nav-group">Company</div>
        <a class="nav-link" routerLink="/members" routerLinkActive="active">
          <app-icon name="users" /> Team
        </a>
        @if (auth.isAdmin()) {
          <a class="nav-link" routerLink="/audit" routerLinkActive="active">
            <app-icon name="scroll" /> Audit log
          </a>
          <a class="nav-link" routerLink="/settings" routerLinkActive="active">
            <app-icon name="building" /> Settings
          </a>
        }

        <div class="sidebar-foot">
          <app-profile-menu />
        </div>
      </aside>

      <main class="main"><router-outlet /></main>
    </div>
  `,
})
export class Shell {
  protected readonly auth = inject(Auth);

  private readonly tickets = httpResource<Ticket[]>(() =>
    this.auth.orgId() ? { url: '/v1/tickets', params: { status: 'open' } } : undefined,
  );
  private readonly conversations = httpResource<Conversation[]>(() =>
    this.auth.orgId() ? '/v1/conversations' : undefined,
  );
  private readonly approvals = httpResource<Approval[]>(() =>
    this.auth.orgId() && this.auth.isAdmin()
      ? { url: '/v1/approvals', params: { status: 'pending' } }
      : undefined,
  );

  protected readonly openTickets = computed(() => this.tickets.value()?.length ?? 0);
  protected readonly pendingApprovals = computed(() => this.approvals.value()?.length ?? 0);
  protected readonly openIssues = computed(
    () =>
      this.conversations.value()?.filter((c) => c.kind === 'issue' && c.status !== 'resolved')
        .length ?? 0,
  );

  constructor() {
    const timer = setInterval(() => {
      this.tickets.reload();
      this.conversations.reload();
      this.approvals.reload();
    }, COUNTS_POLL_MS);
    inject(DestroyRef).onDestroy(() => clearInterval(timer));
  }
}
