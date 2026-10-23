import { httpResource } from '@angular/common/http';
import { Component, DestroyRef, computed, inject, signal } from '@angular/core';
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
      <header class="mobile-bar">
        <button
          type="button"
          class="mobile-menu-button"
          [attr.aria-expanded]="navOpen()"
          aria-label="Toggle navigation"
          aria-controls="staff-navigation"
          (click)="navOpen.update((open) => !open)"
        >
          <app-icon [name]="navOpen() ? 'x' : 'menu'" [size]="19" />
        </button>
        <span class="mobile-brand"><span class="brand-mark">A</span>AgentFlow</span>
        <span class="mobile-org truncate">{{ auth.membership()?.org_name }}</span>
      </header>

      <aside id="staff-navigation" class="sidebar" [class.sidebar-open]="navOpen()">
        <div class="brand">
          <span class="brand-mark">A</span>
          AgentFlow
        </div>

        <a
          class="nav-link"
          routerLink="/insights"
          routerLinkActive="active"
          (click)="navOpen.set(false)"
        >
          <app-icon name="chart" /> Overview
        </a>

        <div class="nav-group">Customers</div>
        <a
          class="nav-link"
          routerLink="/chats"
          routerLinkActive="active"
          (click)="navOpen.set(false)"
        >
          <app-icon name="inbox" /> Inbox
          @if (openIssues()) {
            <span class="count calm" title="Open issues">{{ openIssues() }}</span>
          }
        </a>
        <a
          class="nav-link"
          routerLink="/tickets"
          routerLinkActive="active"
          (click)="navOpen.set(false)"
        >
          <app-icon name="ticket" /> Tickets
          @if (openTickets()) {
            <span class="count" title="Open tickets">{{ openTickets() }}</span>
          }
        </a>
        @if (auth.isAdmin()) {
          <a
            class="nav-link"
            routerLink="/approvals"
            routerLinkActive="active"
            (click)="navOpen.set(false)"
          >
            <app-icon name="check-circle" /> Approvals
            @if (pendingApprovals()) {
              <span class="count" title="Waiting for a decision">{{ pendingApprovals() }}</span>
            }
          </a>
        }

        <div class="nav-group">Operations</div>
        @if (auth.isAdmin()) {
          <a
            class="nav-link"
            routerLink="/bookings"
            routerLinkActive="active"
            (click)="navOpen.set(false)"
          >
            <app-icon name="ticket" /> Seat bookings
          </a>
        }
        <a
          class="nav-link"
          routerLink="/workflows"
          routerLinkActive="active"
          (click)="navOpen.set(false)"
        >
          <app-icon name="activity" /> Requests
        </a>
        <a
          class="nav-link"
          routerLink="/knowledge"
          routerLinkActive="active"
          (click)="navOpen.set(false)"
        >
          <app-icon name="book" /> Knowledge
        </a>
        @if (auth.isAdmin()) {
          <a
            class="nav-link"
            routerLink="/data"
            routerLinkActive="active"
            (click)="navOpen.set(false)"
          >
            <app-icon name="database" /> Data
          </a>
        }

        <div class="nav-group">Company</div>
        <a
          class="nav-link"
          routerLink="/members"
          routerLinkActive="active"
          (click)="navOpen.set(false)"
        >
          <app-icon name="users" /> Team
        </a>
        @if (auth.isAdmin()) {
          <a
            class="nav-link"
            routerLink="/audit"
            routerLinkActive="active"
            (click)="navOpen.set(false)"
          >
            <app-icon name="scroll" /> Audit log
          </a>
          <a
            class="nav-link"
            routerLink="/settings"
            routerLinkActive="active"
            (click)="navOpen.set(false)"
          >
            <app-icon name="building" /> Settings
          </a>
        }

        <div class="sidebar-foot">
          <app-profile-menu />
        </div>
      </aside>

      <main class="main"><router-outlet /></main>
      @if (navOpen()) {
        <button
          type="button"
          class="nav-scrim"
          aria-label="Close navigation"
          (click)="navOpen.set(false)"
        ></button>
      }
    </div>
  `,
  host: { '(document:keydown.escape)': 'navOpen.set(false)' },
})
export class Shell {
  protected readonly auth = inject(Auth);
  protected readonly navOpen = signal(false);

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
