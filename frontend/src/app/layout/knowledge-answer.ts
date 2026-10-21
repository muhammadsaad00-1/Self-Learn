import { Component, input } from '@angular/core';

import type { KnowledgeAnswer } from '../api/types';

/** A knowledge-agent answer with its status and numbered citations. Used by the Knowledge
 *  page (ask a question) and the workflow detail (answer to a customer request). */
@Component({
  selector: 'app-knowledge-answer',
  template: `
    @let a = answer();
    <div class="answer" [attr.data-status]="a.status">
      <div class="answer-head">
        <span class="badge" [attr.data-status]="statusBadge[a.status]">{{
          statusLabel[a.status]
        }}</span>
        @if (a.queries.length) {
          <small class="muted">searched: {{ a.queries.join(' · ') }}</small>
        }
      </div>
      @if (a.answer) {
        <p>{{ a.answer }}</p>
      }
      @if (a.reason) {
        <p class="muted">
          <small>{{ a.reason }}</small>
        </p>
      }
      @if (a.citations.length) {
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
      }
    </div>
  `,
})
export class KnowledgeAnswerView {
  readonly answer = input.required<KnowledgeAnswer>();

  protected readonly statusLabel: Record<KnowledgeAnswer['status'], string> = {
    answered: 'verified answer',
    abstained: 'no answer in the documents',
    unverified: 'unverified: check before use',
  };
  protected readonly statusBadge: Record<KnowledgeAnswer['status'], string> = {
    answered: 'completed',
    abstained: 'queued',
    unverified: 'failed',
  };
}
