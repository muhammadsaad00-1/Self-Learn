import { DatePipe } from '@angular/common';
import { HttpClient, httpResource } from '@angular/common/http';
import { Component, DestroyRef, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { firstValueFrom } from 'rxjs';

import type { Chat, Conversation, LedgerAccount, OpenedChat } from '../api/types';
import { Auth } from '../core/auth';
import { errorMessage } from '../core/errors';
import { ChatThread } from '../layout/chat-thread';

const POLL_MS = 2500;

/** The team's inbox of customer chats: watch the agents work, take over, hand back. */
@Component({
  selector: 'app-conversations-page',
  imports: [DatePipe, FormsModule, ChatThread],
  template: `
    <h1>Chats</h1>
    <div class="row">
      <select [(ngModel)]="customerRef" name="customer" aria-label="Customer">
        <option value="">Open the customer app as…</option>
        @for (a of accounts.value() ?? []; track a.customer_ref) {
          <option [value]="a.customer_ref">{{ a.customer_ref }} · {{ a.customer_name }}</option>
        }
      </select>
      <button (click)="openCustomerApp()" [disabled]="!customerRef || busy()">
        Open customer app ↗
      </button>
    </div>
    <p class="muted">
      Customers sign in with their mobile number at
      <code>{{ portalLink() }}</code>
    </p>
    @if (error()) {
      <p class="error" role="alert">{{ error() }}</p>
    }

    <div class="inbox-split">
      <ul class="list">
        @for (c of conversations.value() ?? []; track c.id) {
          <li>
            <button class="list-item" [class.selected]="c.id === selected()" (click)="select(c.id)">
              <span class="answer-head">
                <strong>{{ c.customer_name ?? c.customer_ref }}</strong>
                <span
                  class="badge"
                  [attr.data-status]="c.mode === 'human' ? 'queued' : 'completed'"
                >
                  {{ c.mode === 'human' ? 'you' : 'AI' }}
                </span>
              </span>
              <small class="muted">
                {{ c.last_message?.body?.slice(0, 70) ?? 'No messages yet' }}
              </small>
              <small class="muted">{{ c.updated_at | date: 'short' }}</small>
            </button>
          </li>
        } @empty {
          <li class="muted">No chats yet. Open the customer app to start one.</li>
        }
      </ul>

      <section class="card detail inbox">
        @if (thread(); as t) {
          <div class="answer-head">
            <strong>{{ t.customer_name }}</strong>
            @if (t.mode === 'human') {
              <span class="muted">You're handling this chat: the agents are paused.</span>
              <button (click)="handBack()" [disabled]="busy()">Hand back to AI</button>
            } @else {
              <span class="muted">The agents are answering. Writing a reply takes over.</span>
            }
          </div>
          <app-chat-thread [messages]="t.messages" [thinking]="t.thinking" mine="agent" />
          <form class="composer" (submit)="$event.preventDefault(); reply()">
            <input
              [(ngModel)]="draft"
              name="reply"
              placeholder="Reply as the support team"
              maxlength="2000"
            />
            <button type="submit" [disabled]="!draft.trim() || busy()">Send</button>
          </form>
        } @else {
          <p class="muted">Select a chat.</p>
        }
      </section>
    </div>
  `,
})
export class ConversationsPage {
  private readonly http = inject(HttpClient);
  private readonly auth = inject(Auth);

  protected customerRef = '';
  protected draft = '';
  protected readonly busy = signal(false);
  protected readonly error = signal<string | null>(null);
  protected readonly selected = signal<string | null>(null);
  protected readonly thread = signal<Chat | null>(null);

  protected portalLink(): string {
    return `${location.origin}/customer/login?bank=${this.auth.orgId()}`;
  }

  protected readonly accounts = httpResource<LedgerAccount[]>(() =>
    this.auth.orgId() ? '/v1/ledger/accounts' : undefined,
  );
  protected readonly conversations = httpResource<Conversation[]>(() =>
    this.auth.orgId() ? '/v1/conversations' : undefined,
  );

  constructor() {
    const timer = setInterval(() => {
      this.conversations.reload();
      void this.loadThread();
    }, POLL_MS);
    inject(DestroyRef).onDestroy(() => clearInterval(timer));
  }

  protected select(id: string): void {
    this.selected.set(id);
    this.thread.set(null);
    void this.loadThread();
  }

  private async loadThread(): Promise<void> {
    const id = this.selected();
    if (!id) return;
    const chat = await firstValueFrom(this.http.get<Chat>(`/v1/conversations/${id}`));
    if (this.selected() === id) this.thread.set(chat);
  }

  protected async openCustomerApp(): Promise<void> {
    // Open the tab synchronously (popup blockers), then point it at the chat.
    const tab = window.open('', '_blank');
    await this.run(async () => {
      const opened = await firstValueFrom(
        this.http.post<OpenedChat>('/v1/conversations', { customer_ref: this.customerRef }),
      );
      if (tab) tab.location.href = `/chat#${opened.customer_token}`;
      this.selected.set(opened.conversation.id);
      this.conversations.reload();
      await this.loadThread();
    });
    if (this.error()) tab?.close();
  }

  protected async reply(): Promise<void> {
    const id = this.selected();
    if (!id) return;
    await this.run(async () => {
      await firstValueFrom(
        this.http.post(`/v1/conversations/${id}/messages`, { text: this.draft }),
      );
      this.draft = '';
      await this.loadThread();
    });
  }

  protected async handBack(): Promise<void> {
    await this.run(async () => {
      this.thread.set(
        await firstValueFrom(
          this.http.post<Chat>(`/v1/conversations/${this.selected()}/handback`, {}),
        ),
      );
    });
  }

  private async run(action: () => Promise<void>): Promise<void> {
    this.busy.set(true);
    this.error.set(null);
    try {
      await action();
    } catch (err) {
      this.error.set(errorMessage(err));
    } finally {
      this.busy.set(false);
    }
  }
}
