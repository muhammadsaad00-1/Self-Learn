import { DatePipe, DecimalPipe } from '@angular/common';
import { Component, ElementRef, effect, input, output, viewChild } from '@angular/core';

import type { ChatMessage } from '../api/types';
import { Icon } from './icon';

/** The facts the backend attaches to a message (conversations.service.outcome_card). */
interface Card {
  voice?: boolean;
  refund?: { amount: string; currency: string; reference: string };
  transfer?: {
    to: string;
    amount: string;
    currency: string;
    reference: string;
    network_status: 'credited' | 'pending' | 'failed';
    reversed: boolean;
  };
  card_frozen?: boolean;
  review?: { amount: string | null };
  case?: { code: string; priority: string; resolved?: boolean };
  sources?: string[];
  /** The orchestrator moved this problem to its own issue thread. */
  issue?: { id: string; subject: string };
  /** The customer's own transactions the answer is based on (answer_from_records). */
  records?: {
    reference: string;
    kind: 'debit' | 'credit' | 'trip';
    trip_ref?: string;
    origin?: string;
    destination?: string;
    departs_at?: string;
    seats_available?: number;
    fare?: string;
    status?: string;
    notes?: string | null;
    amount: string;
    currency: string;
    merchant: string;
    transfer_status: 'credited' | 'pending' | 'failed' | null;
    occurred_at: string;
  }[];
}

/** Chat bubbles with their fact cards (refund, transfer trace, frozen card, case number).
 * Shared by the customer app and the team's inbox. */
@Component({
  selector: 'app-chat-thread',
  imports: [DatePipe, DecimalPipe, Icon],
  template: `
    <div class="thread" #scroller>
      @for (m of messages(); track m.id) {
        <div class="bubble-row" [attr.data-role]="m.role" [class.mine]="m.role === mine()">
          <div class="bubble">
            @if (m.role === 'agent') {
              <small class="who">Support team</small>
            }
            @if (card(m)?.voice) {
              <small class="who"><app-icon name="mic" [size]="12" /> Voice note</small>
            }
            <p>{{ m.body }}</p>

            @if (card(m); as c) {
              @if (c.transfer; as t) {
                <div class="fact transfer">
                  <strong>{{ t.currency }} {{ t.amount | number: '1.0-0' }} → {{ t.to }}</strong>
                  <ol class="trace">
                    <li class="done">Debited from your account</li>
                    <li
                      [class.bad]="t.network_status === 'failed'"
                      [class.wait]="t.network_status === 'pending'"
                    >
                      @switch (t.network_status) {
                        @case ('failed') {
                          Rejected by the recipient's bank
                        }
                        @case ('pending') {
                          Stuck in transit at the recipient's bank
                        }
                        @default {
                          Credited to the recipient
                        }
                      }
                    </li>
                    @if (t.reversed) {
                      <li class="done">Money returned to you</li>
                    }
                  </ol>
                </div>
              }
              @if (c.issue; as i) {
                <button type="button" class="fact issue-link" (click)="openIssue.emit(i.id)">
                  <app-icon name="ticket" [size]="13" /> Issue opened:
                  <strong>{{ i.subject }}</strong> · open →
                </button>
              }
              @if (c.records; as rs) {
                <div class="fact records">
                  @for (r of rs; track $index) {
                    @if (r.kind === 'trip') {
                      <div class="record">
                        <span>
                          {{ r.origin }} → {{ r.destination }}
                          <small class="muted"
                            >{{ r.departs_at | date: 'EEE MMM d, h:mm a' }} · {{ r.status
                            }}{{ r.notes ? ' · ' + r.notes : '' }}</small
                          >
                        </span>
                        <span>
                          <strong>{{ r.seats_available }} seats</strong>
                          <small class="muted">PKR {{ +(r.fare ?? 0) | number: '1.0-0' }}</small>
                        </span>
                      </div>
                    } @else {
                      <div class="record">
                        <span>
                          {{ r.merchant }}
                          <small class="muted"
                            >{{ r.occurred_at | date: 'MMM d, h:mm a' }} · {{ r.reference }}</small
                          >
                        </span>
                        <span>
                          <strong
                            >{{ r.kind === 'credit' ? '+' : '−'
                            }}{{ +r.amount | number: '1.0-0' }}</strong
                          >
                          @if (r.transfer_status === 'credited') {
                            <small class="ok">delivered</small>
                          }
                        </span>
                      </div>
                    }
                  }
                </div>
              }
              @if (c.refund; as r) {
                <div class="fact good">
                  <app-icon name="check" [size]="13" /> {{ r.currency }}
                  {{ r.amount | number: '1.0-0' }} returned
                  <code>{{ r.reference }}</code>
                </div>
              }
              @if (c.card_frozen) {
                <div class="fact warn">
                  <app-icon name="lock" [size]="13" /> Card frozen: no one can use it
                </div>
              }
              @if (c.review) {
                <div class="fact wait">
                  <app-icon name="clock" [size]="13" /> With an officer for sign-off
                </div>
              }
              @if (c.case; as k) {
                @if (k.resolved) {
                  <div class="fact done">
                    <app-icon name="check-circle" [size]="13" /> Case <code>{{ k.code }}</code> ·
                    resolved
                  </div>
                } @else {
                  <div class="fact">
                    <app-icon name="user" [size]="13" /> Case <code>{{ k.code }}</code> · a
                    specialist is on it
                  </div>
                }
              }
              @if (c.sources; as s) {
                <small class="sources"
                  ><app-icon name="book" [size]="11" /> {{ s.join(' · ') }}</small
                >
              }
            }
            <small class="time">{{ m.created_at | date: 'shortTime' }}</small>
          </div>
        </div>
      }
      @if (thinking()) {
        <div class="bubble-row">
          <div class="bubble typing" aria-label="Typing"><i></i><i></i><i></i></div>
        </div>
      }
    </div>
  `,
})
export class ChatThread {
  readonly messages = input.required<ChatMessage[]>();
  readonly thinking = input(false);
  /** Whose bubbles sit on the right: the customer in the app, the team in the inbox. */
  readonly mine = input<ChatMessage['role']>('customer');
  /** The customer (or staff member) clicked an "issue opened" card. */
  readonly openIssue = output<string>();

  protected card(m: ChatMessage): Card | null {
    return m.card as Card | null;
  }

  private readonly scroller = viewChild.required<ElementRef<HTMLElement>>('scroller');

  constructor() {
    // Stick to the newest message.
    effect(() => {
      this.messages();
      this.thinking();
      const el = this.scroller().nativeElement;
      queueMicrotask(() => (el.scrollTop = el.scrollHeight));
    });
  }
}
