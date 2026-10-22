import { HttpClient, HttpErrorResponse } from '@angular/common/http';
import { Component, DestroyRef, inject, output, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { firstValueFrom } from 'rxjs';

import type { Chat } from '../api/types';
import { CustomerSession } from '../core/customer-session';
import { errorMessage } from '../core/errors';
import { ChatThread } from './chat-thread';

const POLL_MS = 1500;

/** Starter questions for an empty chat: one per thing the agents can do. */
const SUGGESTIONS = [
  'How do I issue a cheque? 🧾',
  'Maine Ali ko 5000 bheje, kat gaye lekin usko nahi mile 😟',
  "I sent 3000 to Sara yesterday, she says it didn't come",
  "I see payments I didn't make!! 😱",
];

/** The customer's chat with the bank: text or voice notes, polled while the agents work. */
@Component({
  selector: 'app-customer-chat',
  imports: [FormsModule, ChatThread],
  template: `
    @if (chat(); as c) {
      @if (c.messages.length === 0) {
        <div class="hello">
          <p>
            Hi {{ c.customer_name?.split(' ')?.[0] ?? 'there' }} 👋 Text or send a voice note, in
            English or Urdu. I can trace transfers, check your transactions, fix wrong charges,
            freeze your card and answer banking questions.
          </p>
          <div class="chips">
            @for (s of suggestions; track s) {
              <button class="chip" (click)="send(s)" [disabled]="busy()">{{ s }}</button>
            }
          </div>
        </div>
      }
      <app-chat-thread [messages]="c.messages" [thinking]="c.thinking || busy()" />
    } @else {
      <p class="muted hello">Loading…</p>
    }
    @if (notice()) {
      <p class="error hello" role="alert">{{ notice() }}</p>
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
  `,
})
export class CustomerChat {
  private readonly http = inject(HttpClient);
  private readonly session = inject(CustomerSession);

  /** The agents or the team answered: the portal refreshes cases and card status. */
  readonly answered = output<Chat>();

  protected readonly suggestions = SUGGESTIONS;
  protected readonly chat = signal<Chat | null>(null);
  protected readonly busy = signal(false);
  protected readonly recording = signal(false);
  protected readonly notice = signal<string | null>(null);
  protected draft = '';

  private recorder: MediaRecorder | null = null;

  constructor() {
    void this.refresh();
    const timer = setInterval(() => void this.refresh(), POLL_MS);
    inject(DestroyRef).onDestroy(() => clearInterval(timer));
  }

  private async refresh(): Promise<void> {
    if (!this.session.loggedIn()) return;
    try {
      const next = await firstValueFrom(
        this.http.get<Chat>('/v1/chat', { headers: this.session.headers() }),
      );
      const before = this.chat()?.messages.length;
      this.chat.set(next);
      if (before !== undefined && next.messages.length !== before) this.answered.emit(next);
    } catch (err) {
      if (err instanceof HttpErrorResponse && err.status === 401) this.session.end();
    }
  }

  protected async send(text: string): Promise<void> {
    text = text.trim();
    if (!text || this.busy()) return;
    await this.post('/v1/chat/messages', { text }, () => (this.draft = ''));
  }

  protected async toggleRecording(): Promise<void> {
    if (this.recorder) {
      this.recorder.stop(); // the 'stop' handler uploads
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
        const form = new FormData();
        const name = recorder.mimeType.includes('mp4') ? 'voice.m4a' : 'voice.webm';
        form.append('file', new Blob(parts, { type: recorder.mimeType }), name);
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
      await firstValueFrom(this.http.post(url, body, { headers: this.session.headers() }));
      onDone?.();
      await this.refresh();
    } catch (err) {
      this.flash(errorMessage(err));
    } finally {
      this.busy.set(false);
    }
  }
}
