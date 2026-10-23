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

type Tab = 'help' | 'issues' | 'activity' | 'bookings';
interface BookingTrip {
  id: string;
  trip_ref: string;
  origin: string;
  destination: string;
  departs_at: string;
  fare: string;
  currency: string;
  seats_available: number;
}
interface SeatBooking {
  id: string;
  trip_ref: string | null;
  booking_ref: string;
  seats: number;
  unit_fare: string;
  currency: string;
  status: string;
  expires_at: string | null;
  created_at: string;
}

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
            @case ('bookings') {
              <form class="report stack-sm" (submit)="$event.preventDefault(); bookSeat()">
                <label
                  >Trip<select [(ngModel)]="tripId" name="trip" required>
                    <option value="">Choose a departure</option>
                    @for (t of trips.value() ?? []; track t.id) {
                      <option [value]="t.id">
                        {{ t.origin }} → {{ t.destination }} ·
                        {{ t.departs_at | date: 'MMM d, h:mm a' }} · {{ t.seats_available }} seats ·
                        {{ t.currency }} {{ +t.fare | number: '1.0-0' }}
                      </option>
                    }
                  </select></label
                >
                <label
                  >Phone number<input
                    [(ngModel)]="bookingPhone"
                    name="bookingPhone"
                    inputmode="tel"
                    autocomplete="tel"
                    required
                /></label>
                <label
                  >CNIC (13 digits)<input
                    [(ngModel)]="bookingCnic"
                    name="bookingCnic"
                    inputmode="numeric"
                    maxlength="20"
                    autocomplete="off"
                    required
                /></label>
                <label
                  >Seats<input
                    [(ngModel)]="bookingSeats"
                    name="bookingSeats"
                    type="number"
                    min="1"
                    max="6"
                    required
                /></label>
                <button type="submit" [disabled]="busy() || !tripId">
                  {{ busy() ? 'Checking…' : 'Verify and hold seats' }}
                </button>
                <small class="muted"
                  >We compare these details with your customer record. A seat hold expires after 20
                  minutes until payment is confirmed through the operator.</small
                >
                @if (error()) {
                  <small class="error" role="alert">{{ error() }}</small>
                }
              </form>
              <ul class="activity">
                @for (b of bookings.value() ?? []; track b.id) {
                  <li>
                    <span
                      ><strong>{{ b.booking_ref }} · {{ b.trip_ref ?? 'Trip' }}</strong
                      ><small class="muted"
                        >{{ b.seats }} seat(s) · {{ b.status }}
                        @if (b.expires_at) {
                          · expires {{ b.expires_at | date: 'shortTime' }}
                        }
                      </small></span
                    >
                    @if (b.status === 'held' || b.status === 'confirmed') {
                      <button type="button" class="ghost" (click)="cancelBooking(b)">Cancel</button>
                    }
                  </li>
                } @empty {
                  <li class="muted">Your seat bookings will appear here.</li>
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
    { id: 'bookings', label: 'Seats', icon: 'ticket' },
  ];
  protected readonly tab = signal<Tab>('help');
  protected readonly openIssue = signal<string | null>(null);
  protected readonly busy = signal(false);
  protected readonly error = signal<string | null>(null);
  protected readonly transferLabel = TRANSFER_LABEL;
  protected readonly issueStatus = ISSUE_STATUS;
  protected problem = '';
  protected tripId = '';
  protected bookingPhone = '';
  protected bookingCnic = '';
  protected bookingSeats = 1;

  private resource<T>(url: string) {
    return httpResource<T>(() =>
      this.session.loggedIn() ? { url, headers: this.session.headers() } : undefined,
    );
  }

  protected readonly me = this.resource<CustomerMe>('/v1/customer/me');
  protected readonly transactions = this.resource<LedgerTransaction[]>('/v1/customer/transactions');
  protected readonly conversations = this.resource<Conversation[]>('/v1/customer/conversations');
  protected readonly trips = this.resource<BookingTrip[]>('/v1/customer/bookings/trips');
  protected readonly bookings = this.resource<SeatBooking[]>('/v1/customer/bookings');

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
    if (this.tab() === 'activity') return 'Activity';
    if (this.tab() === 'bookings') return 'Seat bookings';
    return this.me.value()?.bank_name ?? this.session.bankName() ?? 'Your bank';
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
    if (tab === 'bookings') {
      this.trips.reload();
      this.bookings.reload();
    }
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

  protected async bookSeat(): Promise<void> {
    this.busy.set(true);
    this.error.set(null);
    try {
      await firstValueFrom(
        this.http.post(
          '/v1/customer/bookings',
          {
            trip_id: this.tripId,
            phone: this.bookingPhone,
            cnic: this.bookingCnic,
            seats: Number(this.bookingSeats),
            idempotency_key: crypto.randomUUID(),
          },
          { headers: this.session.headers() },
        ),
      );
      this.bookingPhone = '';
      this.bookingCnic = '';
      this.tripId = '';
      this.bookings.reload();
      this.trips.reload();
    } catch (err) {
      this.error.set(errorMessage(err));
    } finally {
      this.busy.set(false);
    }
  }

  protected async cancelBooking(booking: SeatBooking): Promise<void> {
    this.error.set(null);
    try {
      await firstValueFrom(
        this.http.post(
          `/v1/customer/bookings/${booking.id}/cancel`,
          {},
          { headers: this.session.headers() },
        ),
      );
      this.bookings.reload();
      this.trips.reload();
    } catch (err) {
      this.error.set(errorMessage(err));
    }
  }

  protected logout(): void {
    const bank = this.session.bankId();
    this.session.end();
    void this.router.navigateByUrl(bank ? `/customer/login?bank=${bank}` : '/customer/login');
  }
}
