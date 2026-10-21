import { HttpClient, HttpErrorResponse } from '@angular/common/http';
import { Component, DestroyRef, OnInit, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { firstValueFrom } from 'rxjs';

import type { Chat } from '../api/types';
import { errorMessage } from '../core/errors';
import { ChatThread } from '../layout/chat-thread';

const TOKEN_KEY = 'agentflow.customer-token';
const POLL_MS = 1500;

/** Starter questions for an empty chat: one per thing the agents can do. */
const SUGGESTIONS = [
  'How do I issue a cheque? 🧾',
  'Maine Ali ko 5000 bheje, kat gaye lekin usko nahi mile 😟',
  "I see payments I didn't make!! 😱",
  'Cheque book kaise milegi?',
];

/**
 * The customer's app: a texting-first chat with the bank. Opened from a link whose #fragment
 * carries the customer token (fragments never reach servers or logs). No staff session is
 * involved: the interceptor leaves /v1/chat alone.
 */
@Component({
  selector: 'app-chat-page',
  imports: [FormsModule, ChatThread],
  template: `
    <div class="phone">
      <header class="phone-head">
        <div class="avatar">AF</div>
        <div>
          <strong>AgentFlow Bank</strong>
          <small>{{ status() }}</small>
        </div>
      </header>

      @if (chat(); as c) {
        @if (c.messages.length === 0) {
          <div class="hello">
            <p>
              Hi {{ c.customer_name?.split(' ')?.[0] ?? 'there' }} 👋 Text or send a voice note, in
              English or Urdu. I can trace transfers, fix wrong charges, freeze your card and answer
              banking questions.
            </p>
            <div class="chips">
              @for (s of suggestions; track s) {
                <button class="chip" (click)="send(s)" [disabled]="busy()">{{ s }}</button>
              }
            </div>
          </div>
        }
        <app-chat-thread [messages]="c.messages" [thinking]="c.thinking || busy()" />
      } @else if (!error()) {
        <p class="muted hello">Loading…</p>
      }

      @if (error() ?? notice(); as problem) {
        <p class="error hello" role="alert">{{ problem }}</p>
      }

      <form class="phone-composer" (submit)="$event.preventDefault(); send(draft)">
        <input
          [(ngModel)]="draft"
          name="draft"
          placeholder="Message"
          maxlength="2000"
          autocomplete="off"
          [disabled]="!chat() || recording()"
        />
        @if (draft.trim()) {
          <button type="submit" aria-label="Send" [disabled]="busy()">➤</button>
        } @else {
          <button
            type="button"
            [class.recording]="recording()"
            [attr.aria-label]="recording() ? 'Stop and send voice note' : 'Record a voice note'"
            (click)="toggleRecording()"
            [disabled]="!chat() || busy()"
          >
            {{ recording() ? '■' : '🎤' }}
          </button>
        }
      </form>
    </div>
  `,
})
export class ChatPage implements OnInit {
  private readonly http = inject(HttpClient);

  protected readonly suggestions = SUGGESTIONS;
  protected readonly chat = signal<Chat | null>(null);
  protected readonly busy = signal(false);
  protected readonly recording = signal(false);
  protected readonly error = signal<string | null>(null); // fatal: bad or expired link
  protected readonly notice = signal<string | null>(null);
  protected draft = '';

  private token = '';
  private recorder: MediaRecorder | null = null;

  constructor() {
    const timer = setInterval(() => void this.refresh(), POLL_MS);
    inject(DestroyRef).onDestroy(() => clearInterval(timer));
  }

  ngOnInit(): void {
    // The link's fragment wins; sessionStorage keeps the chat across reloads in this tab.
    const fromLink = location.hash.slice(1);
    if (fromLink) {
      sessionStorage.setItem(TOKEN_KEY, fromLink);
      history.replaceState(null, '', location.pathname); // keep the token out of history
    }
    this.token = fromLink || sessionStorage.getItem(TOKEN_KEY) || '';
    if (!this.token) this.error.set('This chat link is missing or incomplete.');
    else void this.refresh();
  }

  protected status(): string {
    const c = this.chat();
    if (c?.thinking || this.busy()) return 'typing…';
    if (c?.mode === 'human') return 'A team member is here';
    return 'Online · 24/7';
  }

  private headers(): Record<string, string> {
    return { Authorization: `Bearer ${this.token}` };
  }

  private async refresh(): Promise<void> {
    if (!this.token || this.error()) return;
    try {
      this.chat.set(
        await firstValueFrom(this.http.get<Chat>('/v1/chat', { headers: this.headers() })),
      );
    } catch (err) {
      if (err instanceof HttpErrorResponse && err.status === 401) {
        sessionStorage.removeItem(TOKEN_KEY);
        this.error.set('This chat link has expired. Ask the bank for a new one.');
      }
    }
  }

  protected async send(text: string): Promise<void> {
    text = text.trim();
    if (!text || this.busy()) return;
    await this.post('/v1/chat/messages', { text }, () => (this.draft = ''));
  }

  protected async toggleRecording(): Promise<void> {
    if (this.recorder) {
      this.recorder.stop(); // 'stop' handler uploads
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const recorder = new MediaRecorder(stream);
      const parts: Blob[] = [];
      recorder.ondataavailable = (e) => parts.push(e.data);
      recorder.onstop = () => {
        stream.getTracks().forEach((t) => t.stop());
        this.recorder = null;
        this.recording.set(false);
        const blob = new Blob(parts, { type: recorder.mimeType });
        const form = new FormData();
        form.append('file', blob, recorder.mimeType.includes('mp4') ? 'voice.m4a' : 'voice.webm');
        void this.post('/v1/chat/voice', form);
      };
      recorder.start();
      this.recorder = recorder;
      this.recording.set(true);
    } catch {
      this.flash('Microphone not available: allow it in the browser, or type instead.');
    }
  }

  /** A passing problem: shown for a few seconds, the chat keeps working. */
  private flash(message: string): void {
    this.notice.set(message);
    setTimeout(() => this.notice.set(null), 4000);
  }

  private async post(url: string, body: unknown, onDone?: () => void): Promise<void> {
    this.busy.set(true);
    try {
      await firstValueFrom(this.http.post(url, body, { headers: this.headers() }));
      onDone?.();
      await this.refresh();
    } catch (err) {
      this.flash(errorMessage(err));
    } finally {
      this.busy.set(false);
    }
  }
}
