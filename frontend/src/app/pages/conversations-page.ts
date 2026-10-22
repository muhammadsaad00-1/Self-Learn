import { HttpClient, httpResource } from '@angular/common/http';
import { Component, DestroyRef, computed, effect, inject, signal, untracked } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { firstValueFrom } from 'rxjs';

import type { Chat, Conversation, LedgerAccount, OpenedChat } from '../api/types';
import { Auth } from '../core/auth';
import { errorMessage } from '../core/errors';
import { FORMAT } from '../core/format';
import { Icon } from '../layout/icon';
import { ChatThread } from '../layout/chat-thread';

const POLL_MS = 2500;

type Filter = 'attention' | 'issues' | 'help' | 'all';

/** The team's inbox: help chats and issue threads. "Needs attention" (open issues and chats a
 *  person took over) is the default view; watch the agents work, take over, hand back. */
@Component({
  selector: 'app-conversations-page',
  imports: [FormsModule, ChatThread, Icon, ...FORMAT],
  template: `
    <div class="page wide">
      <header class="page-header">
        <div>
          <h1>Inbox</h1>
          <p class="subtitle">
            Customers write in the app; the agents answer and open issues. Step in any time.
          </p>
        </div>
        <div class="actions">
          <button type="button" class="secondary" (click)="copyLink()" [title]="portalLink()">
            <app-icon [name]="copied() ? 'check' : 'external'" />
            {{ copied() ? 'Link copied' : 'Copy customer app link' }}
          </button>
          <select [(ngModel)]="customerRef" name="customer" aria-label="Customer">
            <option value="">Open the customer app as…</option>
            @for (a of accounts.value() ?? []; track a.customer_ref) {
              <option [value]="a.customer_ref">{{ a.customer_name }} · {{ a.customer_ref }}</option>
            }
          </select>
          <button type="button" (click)="openCustomerApp()" [disabled]="!customerRef || busy()">
            Open <app-icon name="external" />
          </button>
        </div>
      </header>

      <div class="toolbar">
        <div class="segmented" role="tablist">
          @for (f of filters; track f.id) {
            <button
              type="button"
              role="tab"
              [class.active]="filter() === f.id"
              (click)="filter.set(f.id)"
            >
              {{ f.label }}<span class="count">{{ countOf(f.id) }}</span>
            </button>
          }
        </div>
      </div>
      @if (error()) {
        <div class="callout" data-tone="danger" role="alert" style="margin-bottom: 12px">
          {{ error() }}
        </div>
      }

      <div class="split">
        <section class="card queue">
          @for (c of shown(); track c.id) {
            <button
              type="button"
              class="queue-item"
              [class.selected]="c.id === selected()"
              (click)="select(c.id)"
            >
              <span class="avatar">{{ c.customer_name ?? c.customer_ref | initials }}</span>
              <span class="stack-sm" style="gap: 3px; min-width: 0">
                <span class="line">
                  <span class="title truncate">{{ c.customer_name ?? c.customer_ref }}</span>
                  <span class="time">{{ c.updated_at | ago }}</span>
                </span>
                <span class="line">
                  @if (c.kind === 'issue') {
                    <app-icon name="ticket" [size]="13" />
                    <span class="truncate" style="font-size: 13px">{{
                      c.subject ?? 'New issue'
                    }}</span>
                  } @else {
                    <app-icon name="message" [size]="13" />
                    <span class="truncate muted" style="font-size: 13px">Help chat</span>
                  }
                </span>
                <span class="line">
                  <span class="preview truncate grow">{{
                    c.last_message?.body ?? 'No messages yet'
                  }}</span>
                  @if (c.mode === 'human') {
                    <span class="badge" data-tone="info">You</span>
                  } @else if (c.kind === 'issue') {
                    <span class="badge" [attr.data-tone]="c.status | tone">{{
                      c.status | human
                    }}</span>
                  }
                </span>
              </span>
            </button>
          } @empty {
            <div class="empty">
              <span class="empty-icon"><app-icon name="inbox" [size]="20" /></span>
              <strong>{{ filter() === 'attention' ? 'All caught up' : 'No conversations' }}</strong>
              <span>{{
                filter() === 'attention'
                  ? 'The agents are handling everything right now.'
                  : 'Open the customer app to start one.'
              }}</span>
            </div>
          }
        </section>

        <section class="card detail-sticky inbox-thread">
          @if (thread(); as t) {
            <div class="card-head">
              <span class="avatar lg">{{ t.customer_name ?? t.customer_ref | initials }}</span>
              <div class="grow stack-sm" style="gap: 2px">
                <div class="row">
                  <strong>{{ t.customer_name ?? t.customer_ref }}</strong>
                  <small class="muted">{{ t.customer_ref }}</small>
                  @if (t.kind === 'issue') {
                    <span class="badge" [attr.data-tone]="t.status | tone">{{
                      t.status | human
                    }}</span>
                  }
                  @if (t.case_code) {
                    <span class="badge" data-tone="warning">{{ t.case_code }}</span>
                  }
                </div>
                <small class="muted truncate">{{
                  t.kind === 'issue' ? (t.subject ?? 'Issue') : 'Help chat'
                }}</small>
              </div>
              @if (t.mode === 'human') {
                <span class="badge" data-tone="info">You're handling this</span>
                <button type="button" class="secondary sm" (click)="handBack()" [disabled]="busy()">
                  <app-icon name="bot" /> Hand back to AI
                </button>
              } @else {
                <span class="badge" data-tone="ai"
                  ><app-icon name="sparkles" [size]="12" /> AI answering</span
                >
              }
            </div>
            <div class="card-body stack" style="padding: 12px">
              <app-chat-thread
                [messages]="t.messages"
                [thinking]="t.thinking"
                mine="agent"
                (openIssue)="select($event)"
              />
              <form class="row" (submit)="$event.preventDefault(); reply()">
                <input
                  class="grow"
                  [(ngModel)]="draft"
                  name="reply"
                  [placeholder]="
                    t.mode === 'human'
                      ? 'Reply to ' + (t.customer_name ?? 'the customer')
                      : 'Reply as the support team (takes over from the AI)'
                  "
                  maxlength="2000"
                />
                <button type="submit" [disabled]="!draft.trim() || busy()">
                  <app-icon name="send" /> Send
                </button>
              </form>
            </div>
          } @else {
            <div class="empty">
              <span class="empty-icon"><app-icon name="message" [size]="20" /></span>
              <strong>Select a conversation</strong>
              <span>Watch the agents answer, or reply to take over.</span>
            </div>
          }
        </section>
      </div>
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
  protected readonly filters: { id: Filter; label: string }[] = [
    { id: 'attention', label: 'Needs attention' },
    { id: 'issues', label: 'Issues' },
    { id: 'help', label: 'Help chats' },
    { id: 'all', label: 'All' },
  ];
  protected readonly filter = signal<Filter>('attention');
  protected readonly copied = signal(false);
  protected readonly shown = computed(() => this.byFilter(this.filter()));

  protected countOf(f: Filter): number {
    return this.byFilter(f).length;
  }

  private byFilter(f: Filter): Conversation[] {
    const all = this.conversations.value() ?? [];
    switch (f) {
      case 'attention':
        return all.filter(
          (c) => c.mode === 'human' || (c.kind === 'issue' && c.status !== 'resolved'),
        );
      case 'issues':
        return all.filter((c) => c.kind === 'issue');
      case 'help':
        return all.filter((c) => c.kind === 'help');
      default:
        return all;
    }
  }

  protected async copyLink(): Promise<void> {
    try {
      await navigator.clipboard.writeText(this.portalLink());
      this.copied.set(true);
      setTimeout(() => this.copied.set(false), 2000);
    } catch {
      this.error.set(`Customer app link: ${this.portalLink()}`);
    }
  }
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
    // Open the first conversation of the current view, so the page never starts empty.
    effect(() => {
      const first = this.shown()[0];
      if (first && !untracked(this.selected)) this.select(first.id);
    });
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
