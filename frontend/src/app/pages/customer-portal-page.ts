import { DatePipe, DecimalPipe } from '@angular/common';
import { HttpClient, httpResource } from '@angular/common/http';
import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';
import { firstValueFrom } from 'rxjs';

import type { Chat, Conversation, CustomerMe, LedgerTransaction } from '../api/types';
import { CustomerSession } from '../core/customer-session';
import { errorMessage } from '../core/errors';
import { Icon } from '../layout/icon';
import { CustomerChat } from '../layout/customer-chat';

type Tab = 'help' | 'issues' | 'activity';

const TRANSFER_LABEL: Record<string, string> = {
  credited: 'Delivered',
  pending: 'In transit',
  failed: 'Failed',
};

const ISSUE_STATUS: Record<Conversation['status'], { label: string; badge: string }> = {
  open: { label: 'working on it', badge: 'queued' },
  in_review: { label: 'with an officer', badge: 'queued' },
  with_specialist: { label: 'specialist on it', badge: 'queued' },
  resolved: { label: 'resolved', badge: 'completed' },
};

/**
 * The customer's own app (never the staff dashboard):
 * - Help: questions, answered from the bank's documents and the customer's own records.
 * - Issues: one thread per problem, opened by the customer or by the agents when a help
 *   message turns out to be a problem; each shows its status and case number.
 * - Activity: their transactions (the same ledger the agents read).
 * Opened after the OTP login, or from a staff-issued link (/chat#<token>).
 */
@Component({
  selector: 'app-customer-portal-page',
  imports: [DatePipe, DecimalPipe, FormsModule, CustomerChat, Icon],
  template: `
    <div class="phone-stage">
      <div class="phone">
        <header class="phone-head">
          @if (tab() === 'issues' && openIssue()) {
            <button
              type="button"
              class="link on-accent"
              aria-label="Back to issues"
              (click)="openIssue.set(null)"
            >
              <app-icon name="chevron-left" [size]="20" />
            </button>
          }
          <div class="avatar"><app-icon name="zap" [size]="16" /></div>
          <div>
            <strong>{{ title() }}</strong>
            <small>
              {{ me.value()?.customer_name ?? '' }} ·
              @if (me.value()?.card_status === 'frozen') {
                🔒 card frozen
              } @else {
                card active
              }
            </small>
          </div>
          <span class="spacer"></span>
          <button type="button" class="link on-accent" (click)="logout()" aria-label="Sign out">
            <app-icon name="logout" [size]="18" />
          </button>
        </header>

        @if (!session.loggedIn()) {
          <p class="hello">Your session ended. Please sign in again.</p>
        } @else {
          @switch (tab()) {
            @case ('help') {
              @if (helpId(); as id) {
                <app-customer-chat
                  [conversationId]="id"
                  (answered)="refresh()"
                  (openIssue)="showIssue($event)"
                />
              }
            }
            @case ('issues') {
              @if (openIssue(); as id) {
                <app-customer-chat [conversationId]="id" (answered)="refresh()" />
              } @else {
                <form class="report" (submit)="$event.preventDefault(); report()">
                  <textarea
                    [(ngModel)]="problem"
                    name="problem"
                    rows="2"
                    maxlength="2000"
                    placeholder="Something wrong? Describe it, e.g. 'my transfer didn't arrive'"
                  ></textarea>
                  <button type="submit" [disabled]="busy() || !problem.trim()">
                    Report a problem
                  </button>
                  @if (error()) {
                    <small class="error" role="alert">{{ error() }}</small>
                  }
                </form>
                <ul class="activity">
                  @for (i of issues(); track i.id) {
                    <li>
                      <button type="button" class="issue-row" (click)="showIssue(i.id)">
                        <span>
                          <strong>{{ i.subject ?? i.last_message?.body ?? 'New issue' }}</strong>
                          <small class="muted">
                            {{ i.created_at | date: 'MMM d, h:mm a' }}
                            @if (i.case_code) {
                              · case {{ i.case_code }}
                            }
                          </small>
                        </span>
                        <span class="badge" [attr.data-status]="issueStatus[i.status].badge">
                          {{ issueStatus[i.status].label }}
                        </span>
                      </button>
                    </li>
                  } @empty {
                    <li class="muted">
                      No issues. If something's wrong, report it above or just text us.
                    </li>
                  }
                </ul>
              }
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
          }
        }

        <nav class="tabbar">
          @for (t of tabs; track t.id) {
            <button type="button" [class.active]="tab() === t.id" (click)="selectTab(t.id)">
              <app-icon [name]="t.icon" [size]="20" />
              {{ t.label }}
              @if (t.id === 'issues' && activeIssues()) {
                <span class="count">{{ activeIssues() }}</span>
              }
            </button>
          }
        </nav>
      </div>
    </div>
  `,
})
export class CustomerPortalPage implements OnInit {
  protected readonly session = inject(CustomerSession);
  private readonly router = inject(Router);
  private readonly http = inject(HttpClient);

  protected readonly tabs: { id: Tab; label: string; icon: string }[] = [
    { id: 'help', label: 'Help', icon: 'message' },
    { id: 'issues', label: 'Issues', icon: 'ticket' },
    { id: 'activity', label: 'Activity', icon: 'activity' },
  ];
  protected readonly tab = signal<Tab>('help');
  protected readonly openIssue = signal<string | null>(null);
  protected readonly busy = signal(false);
  protected readonly error = signal<string | null>(null);
  protected readonly transferLabel = TRANSFER_LABEL;
  protected readonly issueStatus = ISSUE_STATUS;
  protected problem = '';

  private resource<T>(url: string) {
    return httpResource<T>(() =>
      this.session.loggedIn() ? { url, headers: this.session.headers() } : undefined,
    );
  }

  protected readonly me = this.resource<CustomerMe>('/v1/customer/me');
  protected readonly transactions = this.resource<LedgerTransaction[]>('/v1/customer/transactions');
  protected readonly conversations = this.resource<Conversation[]>('/v1/customer/conversations');

  protected readonly helpId = computed(
    () => this.conversations.value()?.find((c) => c.kind === 'help')?.id ?? null,
  );
  protected readonly issues = computed(
    () => this.conversations.value()?.filter((c) => c.kind === 'issue') ?? [],
  );
  protected readonly activeIssues = computed(
    () => this.issues().filter((i) => i.status !== 'resolved').length,
  );
  protected readonly title = computed(() => {
    if (this.tab() === 'issues') {
      const open = this.issues().find((i) => i.id === this.openIssue());
      return open ? (open.subject ?? 'Issue') : 'Your issues';
    }
    return this.tab() === 'activity' ? 'Activity' : (this.me.value()?.bank_name ?? 'Your bank');
  });

  ngOnInit(): void {
    // A staff-issued link carries the token in the #fragment (never sent to servers).
    const fromLink = location.hash.slice(1);
    if (fromLink) {
      this.session.start(fromLink);
      history.replaceState(null, '', '/customer'); // keep the token out of history
    }
    if (!this.session.loggedIn()) void this.router.navigateByUrl('/customer/login');
  }

  protected selectTab(tab: Tab): void {
    this.tab.set(tab);
    if (tab === 'issues') this.openIssue.set(null);
  }

  protected showIssue(id: string): void {
    this.tab.set('issues');
    this.openIssue.set(id);
    this.conversations.reload();
  }

  /** The chat moved on (a refund, a frozen card, a new issue): reload the rest of the app. */
  protected refresh(): void {
    this.me.reload();
    this.transactions.reload();
    this.conversations.reload();
  }

  protected async report(): Promise<void> {
    this.busy.set(true);
    this.error.set(null);
    try {
      const issue = await firstValueFrom(
        this.http.post<Chat>(
          '/v1/customer/issues',
          { text: this.problem.trim() },
          { headers: this.session.headers() },
        ),
      );
      this.problem = '';
      this.showIssue(issue.id);
    } catch (err) {
      this.error.set(errorMessage(err));
    } finally {
      this.busy.set(false);
    }
  }

  protected logout(): void {
    const bank = this.session.bankId();
    this.session.end();
    void this.router.navigateByUrl(bank ? `/customer/login?bank=${bank}` : '/customer/login');
  }
}
