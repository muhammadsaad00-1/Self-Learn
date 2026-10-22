import { DecimalPipe } from '@angular/common';
import { Component, computed, input } from '@angular/core';
import { RouterLink } from '@angular/router';

import type { WorkflowResult } from '../api/types';
import { FORMAT } from '../core/format';
import { Icon } from './icon';
import { KnowledgeAnswerView } from './knowledge-answer';

/** What the system did with a request, as a timeline a reviewer or auditor can read top to
 *  bottom: understood -> policy -> ledger -> risk -> proposal -> decision -> approval ->
 *  execution -> ticket -> reply. AI steps are violet, code decisions green/amber/red. */
@Component({
  selector: 'app-decision-trail',
  imports: [DecimalPipe, RouterLink, Icon, KnowledgeAnswerView, ...FORMAT],
  template: `
    @let r = result();
    <ol class="timeline">
      @if (r.triage; as t) {
        <li>
          <span class="node" data-tone="ai"><app-icon name="sparkles" [size]="14" /></span>
          <div class="body">
            <div class="row">
              <strong>Understood</strong>
              <span class="badge">{{ t.category | human }}</span>
              <span class="badge" [attr.data-tone]="t.urgency | tone">{{ t.urgency | human }}</span>
            </div>
            <span class="muted">{{ t.summary }}</span>
          </div>
        </li>
      }

      @if (r.knowledge; as k) {
        <li>
          <span class="node" data-tone="ai"><app-icon name="book" [size]="14" /></span>
          <div class="body">
            <strong>Checked the company's documents</strong>
            <app-knowledge-answer [answer]="k" />
          </div>
        </li>
      }

      @if (r.evidence; as ev) {
        <li>
          <span class="node" data-tone="brand"><app-icon name="database" [size]="14" /></span>
          <div class="body">
            <strong>Checked the ledger</strong>
            @if (!ev.account_found) {
              <span class="muted">No customer account identified.</span>
            } @else {
              <span class="muted">
                {{ ev.customer_name }} · {{ ev.customer_ref }} · {{ ev.reversals_last_24h }}
                reversals in 24 h
                @if (ev.duplicate_pairs.length) {
                  · <span class="badge" data-tone="warning">duplicate found</span>
                }
              </span>
              <div class="card table-wrap" style="box-shadow: none">
                <table>
                  <tbody>
                    @for (t of ev.transactions; track t.id) {
                      <tr
                        [style.background]="
                          duplicates().has(t.reference) ? 'var(--warning-soft)' : null
                        "
                      >
                        <td>
                          {{ t.merchant }}
                          <div>
                            <small class="faint mono">{{ t.reference }}</small>
                          </div>
                        </td>
                        <td style="white-space: nowrap; text-align: right">
                          <strong [style.color]="t.kind === 'credit' ? 'var(--success)' : null">
                            {{ t.kind === 'credit' ? '+' : '−' }}{{ +t.amount | number: '1.0-0' }}
                          </strong>
                          <div>
                            <small class="faint">{{ t.occurred_at | ago }}</small>
                          </div>
                        </td>
                        <td>
                          @if (t.transfer_status) {
                            <span class="badge" [attr.data-transfer]="t.transfer_status">{{
                              transfer[t.transfer_status]
                            }}</span>
                          }
                          @if (t.status === 'reversed') {
                            <span class="badge" data-tone="success">reversed</span>
                          }
                        </td>
                      </tr>
                    }
                  </tbody>
                </table>
              </div>
            }
          </div>
        </li>
      }

      @if (r.risk; as k) {
        <li>
          <span
            class="node"
            [attr.data-tone]="k.critical ? 'danger' : k.signals.length ? 'warning' : 'success'"
          >
            <app-icon name="alert" [size]="14" />
          </span>
          <div class="body">
            <div class="row">
              <strong>Risk check</strong>
              <span class="badge" [attr.data-tone]="k.priority | tone"
                >{{ k.priority | human }} · {{ k.score }}/100</span
              >
            </div>
            @if (k.signals.length) {
              <ul class="reasons">
                @for (s of k.signals; track s.id) {
                  <li>
                    {{ s.id | human }} <span class="muted">(+{{ s.points }}) {{ s.detail }}</span>
                  </li>
                }
              </ul>
            } @else {
              <span class="muted">No risk signals.</span>
            }
          </div>
        </li>
      }

      @if (r.proposal; as p) {
        <li>
          <span class="node" data-tone="ai"><app-icon name="bot" [size]="14" /></span>
          <div class="body">
            <div class="row">
              <strong>Proposed: {{ p.action | human }}</strong>
              @if (p.transaction_reference && p.amount) {
                <span class="badge">PKR {{ +p.amount | number: '1.0-0' }}</span>
              }
            </div>
            <span class="muted">{{ p.rationale }}</span>
          </div>
        </li>
      }

      @if (r.decision; as d) {
        <li>
          <span class="node" [attr.data-tone]="d.outcome | tone"
            ><app-icon name="shield" [size]="14"
          /></span>
          <div class="body">
            <div class="row">
              <strong>Policy</strong>
              <span class="badge" [attr.data-tone]="d.outcome | tone">{{ d.outcome | human }}</span>
            </div>
            <ul class="reasons">
              @for (reason of d.reasons; track reason) {
                <li>{{ reason }}</li>
              }
            </ul>
          </div>
        </li>
      }

      @if (r.approval; as a) {
        <li>
          <span class="node" [attr.data-tone]="a.status | tone"
            ><app-icon name="check-circle" [size]="14"
          /></span>
          <div class="body">
            <div class="row">
              <strong>Second officer</strong>
              <span class="badge" [attr.data-tone]="a.status | tone">{{ a.status | human }}</span>
            </div>
          </div>
        </li>
      }

      @if (r.execution; as x) {
        @if (x.status === 'executed' || r.approval) {
          <li>
            <span class="node" [attr.data-tone]="x.status | tone"
              ><app-icon name="zap" [size]="14"
            /></span>
            <div class="body">
              <div class="row">
                <strong>{{ x.status === 'executed' ? 'Done' : 'Not executed' }}</strong>
                @if (x.verified) {
                  <span class="badge" data-tone="success"
                    ><app-icon name="check" [size]="12" /> verified in ledger</span
                  >
                }
              </div>
              <span class="muted">{{ x.detail }}</span>
            </div>
          </li>
        }
      }

      @if (r.ticket; as t) {
        <li>
          <span class="node" data-tone="warning"><app-icon name="ticket" [size]="14" /></span>
          <div class="body">
            <div class="row">
              <strong>Handed to a human</strong>
              <a routerLink="/tickets" class="badge" [attr.data-tone]="t.priority | tone"
                >{{ t.code }} · {{ t.priority | human }}</a
              >
            </div>
            <ul class="reasons">
              @for (reason of t.reasons; track reason) {
                <li>{{ reason }}</li>
              }
            </ul>
          </div>
        </li>
      }

      @if (r.reply) {
        <li>
          <span class="node" data-tone="brand"><app-icon name="message" [size]="14" /></span>
          <div class="body">
            <strong>Replied to the customer</strong>
            <p class="quote">{{ r.reply }}</p>
          </div>
        </li>
      }
    </ol>
  `,
})
export class DecisionTrail {
  readonly result = input.required<WorkflowResult>();

  protected readonly duplicates = computed(
    () => new Set((this.result().evidence?.duplicate_pairs ?? []).flat()),
  );
  protected readonly transfer: Record<string, string> = {
    credited: 'delivered',
    pending: 'in transit',
    failed: 'failed',
  };
}
