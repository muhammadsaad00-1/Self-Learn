import { DatePipe, DecimalPipe } from '@angular/common';
import { Component, ElementRef, effect, input, viewChild } from '@angular/core';

import type { ChatMessage } from '../api/types';

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
  case?: { code: string; priority: string };
  sources?: string[];
}

/** Chat bubbles with their fact cards (refund, transfer trace, frozen card, case number).
 * Shared by the customer app and the team's inbox. */
@Component({
  selector: 'app-chat-thread',
  imports: [DatePipe, DecimalPipe],
  template: `
    <div class="thread" #scroller>
      @for (m of messages(); track m.id) {
        <div class="bubble-row" [attr.data-role]="m.role" [class.mine]="m.role === mine()">
          <div class="bubble">
            @if (m.role === 'agent') {
              <small class="who">Support team</small>
            }
            @if (card(m)?.voice) {
              <small class="who">🎤 Voice note</small>
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
              @if (c.refund; as r) {
                <div class="fact good">
                  ✓ {{ r.currency }} {{ r.amount | number: '1.0-0' }} returned
                  <code>{{ r.reference }}</code>
                </div>
              }
              @if (c.card_frozen) {
                <div class="fact warn">🔒 Card frozen: no one can use it</div>
              }
              @if (c.review) {
                <div class="fact wait">⏳ With an officer for sign-off</div>
              }
              @if (c.case; as k) {
                <div class="fact">
                  🧑‍💼 Case <code>{{ k.code }}</code> · a specialist is on it
                </div>
              }
              @if (c.sources; as s) {
                <small class="sources">From: {{ s.join(', ') }}</small>
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
