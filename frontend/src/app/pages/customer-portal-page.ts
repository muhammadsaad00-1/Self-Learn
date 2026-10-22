import { DatePipe, DecimalPipe } from '@angular/common';
import { HttpClient, httpResource } from '@angular/common/http';
import { Component, OnInit, inject, signal } from '@angular/core';
import { Router } from '@angular/router';

import type { CustomerCase, CustomerMe, LedgerTransaction } from '../api/types';
import { CustomerSession } from '../core/customer-session';
import { CustomerChat } from '../layout/customer-chat';

type Tab = 'chat' | 'activity' | 'cases';

const TRANSFER_LABEL: Record<string, string> = {
  credited: 'Delivered',
  pending: 'In transit',
  failed: 'Failed',
};

/**
 * The customer's own app: chat with the bank, their recent activity (the same ledger the
 * agents read), and the status of their support cases. Opened after the OTP login, or from a
 * staff-issued link (/chat#<token>).
 */
@Component({
  selector: 'app-customer-portal-page',
  imports: [DatePipe, DecimalPipe, CustomerChat],
  template: `
    <div class="phone">
      <header class="phone-head">
        <div class="avatar">AF</div>
        <div>
          <strong>{{ me.value()?.customer_name ?? 'AgentFlow Bank' }}</strong>
          <small>
            AgentFlow Bank ·
            @if (me.value()?.card_status === 'frozen') {
              🔒 card frozen
            } @else {
              card active
            }
          </small>
        </div>
        <span class="spacer"></span>
        <button type="button" class="link on-accent" (click)="logout()">Sign out</button>
      </header>

      @if (!session.loggedIn()) {
        <p class="hello">Your session ended. Please sign in again.</p>
      } @else {
        @switch (tab()) {
          @case ('chat') {
            <app-customer-chat (answered)="refresh()" />
          }
          @case ('activity') {
            <ul class="activity">
              @for (t of transactions.value() ?? []; track t.id) {
                <li>
                  <span>
                    <strong>{{ t.merchant }}</strong>
                    <small class="muted">
                      {{ t.occurred_at | date: 'MMM d, h:mm a' }} · {{ t.reference }}
                      @if (t.transfer_status) {
                        ·
                        <span class="badge" [attr.data-transfer]="t.transfer_status">
                          {{ transferLabel[t.transfer_status] }}
                        </span>
                      }
                      @if (t.status === 'reversed') {
                        · <span class="badge">reversed</span>
                      }
                    </small>
                  </span>
                  <span class="amount" [class.in]="t.kind === 'credit'">
                    {{ t.kind === 'credit' ? '+' : '−' }}{{ +t.amount | number: '1.0-0' }}
                  </span>
                </li>
              } @empty {
                <li class="muted">No activity in the last 30 days.</li>
              }
            </ul>
          }
          @case ('cases') {
            <ul class="activity">
              @for (c of cases.value() ?? []; track c.code) {
                <li>
                  <span>
                    <strong>{{ c.code }}</strong>
                    <small class="muted">
                      {{ (c.category ?? 'request').replaceAll('_', ' ') }} · opened
                      {{ c.created_at | date: 'MMM d' }}
                    </small>
                  </span>
                  <span
                    class="badge"
                    [attr.data-status]="c.status === 'resolved' ? 'completed' : 'queued'"
                  >
                    {{ c.status === 'resolved' ? 'resolved' : 'a specialist is on it' }}
                  </span>
                </li>
              } @empty {
                <li class="muted">No open cases. If something's wrong, just text us.</li>
              }
            </ul>
          }
        }
      }

      <nav class="tabbar">
        @for (t of tabs; track t.id) {
          <button type="button" [class.active]="tab() === t.id" (click)="tab.set(t.id)">
            {{ t.label }}
          </button>
        }
      </nav>
    </div>
  `,
})
export class CustomerPortalPage implements OnInit {
  protected readonly session = inject(CustomerSession);
  private readonly router = inject(Router);
  private readonly http = inject(HttpClient);

  protected readonly tabs: { id: Tab; label: string }[] = [
    { id: 'chat', label: '💬 Chat' },
    { id: 'activity', label: '📊 Activity' },
    { id: 'cases', label: '🗂 Cases' },
  ];
  protected readonly tab = signal<Tab>('chat');
  protected readonly transferLabel = TRANSFER_LABEL;

  private resource<T>(url: string) {
    return httpResource<T>(() =>
      this.session.loggedIn() ? { url, headers: this.session.headers() } : undefined,
    );
  }

  protected readonly me = this.resource<CustomerMe>('/v1/customer/me');
  protected readonly transactions = this.resource<LedgerTransaction[]>('/v1/customer/transactions');
  protected readonly cases = this.resource<CustomerCase[]>('/v1/customer/cases');

  ngOnInit(): void {
    // A staff-issued link carries the token in the #fragment (never sent to servers).
    const fromLink = location.hash.slice(1);
    if (fromLink) {
      this.session.start(fromLink);
      history.replaceState(null, '', '/customer'); // keep the token out of history
    }
    if (!this.session.loggedIn()) void this.router.navigateByUrl('/customer/login');
  }

  /** The chat moved on (a refund, a frozen card, a case): reload the rest of the app. */
  protected refresh(): void {
    this.me.reload();
    this.transactions.reload();
    this.cases.reload();
  }

  protected logout(): void {
    const bank = this.session.bankId();
    this.session.end();
    void this.router.navigateByUrl(bank ? `/customer/login?bank=${bank}` : '/customer/login');
  }
}
