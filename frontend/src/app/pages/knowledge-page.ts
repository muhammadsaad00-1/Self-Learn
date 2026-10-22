import { DecimalPipe } from '@angular/common';
import { HttpClient, httpResource } from '@angular/common/http';
import { Component, effect, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { firstValueFrom } from 'rxjs';

import type { KnowledgeAnswer, KnowledgeDocument } from '../api/types';
import { Auth } from '../core/auth';
import { errorMessage } from '../core/errors';
import { FORMAT } from '../core/format';
import { Icon } from '../layout/icon';
import { KnowledgeAnswerView } from '../layout/knowledge-answer';

const INGESTING: KnowledgeDocument['status'][] = ['pending', 'processing'];
const POLL_MS = 2000;

/** The company's documents the agents answer from (upload, ingestion status, delete), and a
 *  box to ask them a question the way a customer would. */
@Component({
  selector: 'app-knowledge-page',
  imports: [FormsModule, DecimalPipe, KnowledgeAnswerView, Icon, ...FORMAT],
  template: `
    <div class="page">
      <header class="page-header">
        <div>
          <h1>Knowledge</h1>
          <p class="subtitle">
            The documents the agents answer from. Every answer cites its source, and the agents say
            so when the documents don't cover a question.
          </p>
        </div>
      </header>

      <div class="split reverse">
        <section class="card">
          <div class="card-head">
            <h2 class="grow">Documents</h2>
            <small class="muted">{{ (documents.value() ?? []).length }} documents</small>
          </div>
          <div class="card-body stack">
            @if (auth.isAdmin()) {
              <label
                class="dropzone"
                [class.over]="dragging()"
                (dragover)="$event.preventDefault(); dragging.set(true)"
                (dragleave)="dragging.set(false)"
                (drop)="dropped($event)"
              >
                <span class="empty-icon"><app-icon name="upload" [size]="20" /></span>
                @if (file) {
                  <strong>{{ file.name }}</strong>
                  <span>{{ file.size / 1024 | number: '1.0-1' }} KB · ready to upload</span>
                } @else {
                  <strong>Drop a policy document, or click to choose</strong>
                  <span>Markdown, text or PDF, up to 10 MB</span>
                }
                <input
                  class="sr-only"
                  type="file"
                  accept=".md,.markdown,.txt,.pdf"
                  (change)="file = $any($event.target).files?.[0] ?? null"
                  #fileInput
                />
              </label>
              @if (file) {
                <form class="row" (ngSubmit)="upload(fileInput)">
                  <input
                    class="grow"
                    [(ngModel)]="title"
                    name="title"
                    placeholder="Title (optional)"
                    maxlength="300"
                  />
                  <button type="button" class="ghost" (click)="file = null; fileInput.value = ''">
                    Cancel
                  </button>
                  <button type="submit" [disabled]="uploading()">
                    <app-icon name="upload" /> {{ uploading() ? 'Uploading…' : 'Upload' }}
                  </button>
                </form>
              }
            }
            @if (error()) {
              <div class="callout" data-tone="danger" role="alert">{{ error() }}</div>
            }
          </div>
          <div class="table-wrap">
            <table>
              <tbody>
                @for (d of documents.value() ?? []; track d.id) {
                  <tr>
                    <td style="width: 36px">
                      <span class="avatar square sm"><app-icon name="file" [size]="13" /></span>
                    </td>
                    <td>
                      <strong>{{ d.title }}</strong>
                      <div>
                        <small class="muted"
                          >{{ d.filename }} · {{ d.size_bytes / 1024 | number: '1.0-1' }} KB ·
                          {{ d.created_at | ago }}</small
                        >
                      </div>
                      @if (d.error) {
                        <small class="error">{{ d.error }}</small>
                      }
                    </td>
                    <td>
                      <span class="badge dot" [attr.data-tone]="d.status | tone">{{
                        d.status === 'ready' ? d.chunk_count + ' passages' : (d.status | human)
                      }}</span>
                      @if (d.quarantined_chunks) {
                        <span
                          class="badge"
                          data-tone="danger"
                          title="Flagged as prompt injection: never used in answers"
                        >
                          {{ d.quarantined_chunks }} quarantined
                        </span>
                      }
                    </td>
                    <td style="text-align: right">
                      @if (auth.isAdmin()) {
                        <button class="ghost sm" (click)="remove(d)" aria-label="Delete document">
                          Delete
                        </button>
                      }
                    </td>
                  </tr>
                } @empty {
                  <tr>
                    <td colspan="4">
                      <div class="empty">
                        <strong>No documents yet</strong
                        ><span>Upload your FAQ, fees or policy documents.</span>
                      </div>
                    </td>
                  </tr>
                }
              </tbody>
            </table>
          </div>
        </section>

        <section class="card pad stack detail-sticky">
          <div>
            <h2>Try a question</h2>
            <p class="muted">Ask the way a customer would, in English, Urdu or Roman Urdu.</p>
          </div>
          <form class="stack-sm" (ngSubmit)="ask()">
            <textarea
              [(ngModel)]="question"
              name="question"
              rows="3"
              maxlength="2000"
              placeholder="e.g. loan ki qist late ho jaye to kitna jurmana hai?"
            ></textarea>
            <button
              type="submit"
              style="justify-self: end"
              [disabled]="!question.trim() || asking()"
            >
              <app-icon name="sparkles" /> {{ asking() ? 'Thinking…' : 'Ask' }}
            </button>
          </form>
          @if (askError()) {
            <div class="callout" data-tone="danger" role="alert">{{ askError() }}</div>
          }
          @if (asking()) {
            <div class="stack-sm">
              <div class="skeleton"></div>
              <div class="skeleton" style="width: 70%"></div>
            </div>
          } @else if (answer(); as a) {
            <app-knowledge-answer [answer]="a" />
          }
        </section>
      </div>
    </div>
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
  protected readonly dragging = signal(false);

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

  protected dropped(event: DragEvent): void {
    event.preventDefault();
    this.dragging.set(false);
    this.file = event.dataTransfer?.files[0] ?? null;
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
