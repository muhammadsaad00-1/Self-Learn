import { DatePipe, DecimalPipe } from '@angular/common';
import { HttpClient, httpResource } from '@angular/common/http';
import { Component, effect, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { firstValueFrom } from 'rxjs';

import type { KnowledgeAnswer, KnowledgeDocument } from '../api/types';
import { Auth } from '../core/auth';
import { errorMessage } from '../core/errors';
import { KnowledgeAnswerView } from '../layout/knowledge-answer';

const INGESTING: KnowledgeDocument['status'][] = ['pending', 'processing'];
const POLL_MS = 2000;

@Component({
  selector: 'app-knowledge-page',
  imports: [FormsModule, DatePipe, DecimalPipe, KnowledgeAnswerView],
  template: `
    <h1>Knowledge</h1>
    <p class="muted">
      Company policies the agents answer from. Answers cite their sources, and the agents refuse
      when the documents don't cover a question.
    </p>

    <section class="card section">
      <h2>Ask the knowledge agents</h2>
      <form class="composer" (ngSubmit)="ask()">
        <textarea
          [(ngModel)]="question"
          name="question"
          rows="2"
          maxlength="2000"
          placeholder="e.g. How long does a duplicate-charge reversal take?"
        ></textarea>
        <button type="submit" [disabled]="!question.trim() || asking()">
          {{ asking() ? 'Thinking…' : 'Ask' }}
        </button>
      </form>
      @if (askError()) {
        <p class="error" role="alert">{{ askError() }}</p>
      }
      @if (answer(); as a) {
        <app-knowledge-answer [answer]="a" />
      }
    </section>

    <section class="card section">
      <h2>Documents</h2>
      @if (auth.isAdmin()) {
        <form class="row" (ngSubmit)="upload(fileInput)">
          <input
            type="file"
            accept=".md,.markdown,.txt,.pdf"
            (change)="file = $any($event.target).files?.[0] ?? null"
            #fileInput
          />
          <input [(ngModel)]="title" name="title" placeholder="Title (optional)" maxlength="300" />
          <button type="submit" [disabled]="!file || uploading()">
            {{ uploading() ? 'Uploading…' : 'Upload' }}
          </button>
        </form>
        <p class="muted"><small>.md, .txt or .pdf, up to 10 MB.</small></p>
      }
      @if (error()) {
        <p class="error" role="alert">{{ error() }}</p>
      }
      <table>
        <thead>
          <tr>
            <th>Title</th>
            <th>Status</th>
            <th>Chunks</th>
            <th>Size</th>
            <th>Uploaded</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          @for (d of documents.value() ?? []; track d.id) {
            <tr>
              <td>
                {{ d.title }}<br /><small class="muted">{{ d.filename }}</small>
              </td>
              <td>
                <span class="badge" [attr.data-status]="badge(d.status)">{{ d.status }}</span>
                @if (d.error) {
                  <br /><small class="error">{{ d.error }}</small>
                }
              </td>
              <td>
                {{ d.chunk_count }}
                @if (d.quarantined_chunks) {
                  <br /><small
                    class="error"
                    title="Flagged as prompt injection; never used in answers"
                  >
                    {{ d.quarantined_chunks }} quarantined
                  </small>
                }
              </td>
              <td>{{ d.size_bytes / 1024 | number: '1.0-1' }} KB</td>
              <td>{{ d.created_at | date: 'short' }}</td>
              <td>
                @if (auth.isAdmin()) {
                  <button class="link danger" (click)="remove(d)">Delete</button>
                }
              </td>
            </tr>
          } @empty {
            <tr>
              <td colspan="6" class="muted">No documents yet.</td>
            </tr>
          }
        </tbody>
      </table>
    </section>
  `,
})
export class KnowledgePage {
  protected readonly auth = inject(Auth);
  private readonly http = inject(HttpClient);

  protected question = '';
  protected title = '';
  protected file: File | null = null;
  protected readonly answer = signal<KnowledgeAnswer | null>(null);
  protected readonly asking = signal(false);
  protected readonly askError = signal<string | null>(null);
  protected readonly uploading = signal(false);
  protected readonly error = signal<string | null>(null);

  protected readonly documents = httpResource<KnowledgeDocument[]>(() => {
    const orgId = this.auth.orgId();
    return orgId ? { url: '/v1/knowledge/documents', headers: { 'X-Org-Id': orgId } } : undefined;
  });

  constructor() {
    // Poll while any document is still being ingested (reads the value: re-runs per refresh).
    effect((onCleanup) => {
      const docs = this.documents.value() ?? [];
      if (!docs.some((d) => INGESTING.includes(d.status))) return;
      const timer = setTimeout(() => this.documents.reload(), POLL_MS);
      onCleanup(() => clearTimeout(timer));
    });
    effect(() => {
      this.auth.orgId(); // another company: its own answers
      this.answer.set(null);
    });
  }

  protected badge(status: KnowledgeDocument['status']): string {
    return status === 'ready' ? 'completed' : status === 'failed' ? 'failed' : 'queued';
  }

  protected async ask(): Promise<void> {
    this.asking.set(true);
    this.askError.set(null);
    try {
      this.answer.set(
        await firstValueFrom(
          this.http.post<KnowledgeAnswer>('/v1/knowledge/ask', { question: this.question.trim() }),
        ),
      );
    } catch (err) {
      this.askError.set(errorMessage(err));
    } finally {
      this.asking.set(false);
    }
  }

  protected async upload(input?: HTMLInputElement): Promise<void> {
    if (!this.file) return;
    const form = new FormData(); // multipart/form-data: the browser sets the boundary
    form.append('file', this.file);
    if (this.title.trim()) form.append('title', this.title.trim());
    this.uploading.set(true);
    this.error.set(null);
    try {
      await firstValueFrom(this.http.post('/v1/knowledge/documents', form));
      this.file = null;
      this.title = '';
      if (input) input.value = ''; // clear the browser's file picker too
      this.documents.reload();
    } catch (err) {
      this.error.set(errorMessage(err));
    } finally {
      this.uploading.set(false);
    }
  }

  protected async remove(d: KnowledgeDocument): Promise<void> {
    this.error.set(null);
    try {
      await firstValueFrom(this.http.delete(`/v1/knowledge/documents/${d.id}`));
    } catch (err) {
      this.error.set(errorMessage(err));
    }
    this.documents.reload();
  }
}
