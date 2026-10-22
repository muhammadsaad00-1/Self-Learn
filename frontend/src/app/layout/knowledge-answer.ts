import { Component, input } from '@angular/core';

import type { KnowledgeAnswer } from '../api/types';

/** A knowledge-agent answer with its status and numbered citations. Used by the Knowledge
 *  page (ask a question) and the workflow detail (answer to a customer request). */
@Component({
  selector: 'app-knowledge-answer',
  template: `
    @let a = answer();
    <div class="stack-sm" [attr.data-status]="a.status">
      <div class="answer-head">
        <span class="badge" [attr.data-status]="statusBadge[a.status]">{{
          statusLabel[a.status]
        }}</span>
        @if (a.citations.length) {
          <small class="muted"
            >{{ a.citations.length }} source{{ a.citations.length > 1 ? 's' : '' }}</small
          >
        }
      </div>
      @if (a.answer) {
        <p>{{ a.answer }}</p>
      }
      @if (a.reason) {
        <small class="muted">{{ a.reason }}</small>
      }
      @if (a.citations.length) {
        <details>
          <summary class="muted" style="cursor: pointer">Sources and search queries</summary>
          <ol class="citations">
            @for (c of a.citations; track c.chunk_id) {
              <li>
                <strong>[{{ c.number }}] {{ c.document_title }}</strong>
                @if (c.section) {
                  <span class="muted"> › {{ c.section }}</span>
                }
                <blockquote>{{ c.excerpt }}…</blockquote>
              </li>
            }
          </ol>
          @if (a.queries.length) {
            <small class="faint">Searched: {{ a.queries.join(' · ') }}</small>
          }
        </details>
      }
    </div>
  `,
})
export class KnowledgeAnswerView {
  readonly answer = input.required<KnowledgeAnswer>();

  protected readonly statusLabel: Record<KnowledgeAnswer['status'], string> = {
    answered: 'Verified answer',
    abstained: 'Not in the documents',
    unverified: 'Unverified: check before use',
  };
  protected readonly statusBadge: Record<KnowledgeAnswer['status'], string> = {
    answered: 'completed',
    abstained: 'queued',
    unverified: 'failed',
  };
}
