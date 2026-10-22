import { DatePipe } from '@angular/common';
import { Component, computed, input } from '@angular/core';
import { RouterLink } from '@angular/router';

import type { WorkflowResult } from '../api/types';

/** Everything the system did after triage, in order: evidence -> proposal -> policy ->
 *  approval -> execution -> reply. This is what a reviewer or an auditor reads. */
@Component({
  selector: 'app-decision-trail',
  imports: [DatePipe, RouterLink],
  template: `
    @let r = result();
    @if (r.evidence; as ev) {
      <h3>Ledger evidence</h3>
      @if (!ev.account_found) {
        <p class="muted">No customer account identified.</p>
      } @else {
        <p class="muted">
          {{ ev.customer_name }} ({{ ev.customer_ref }}) · {{ ev.reversals_last_24h }} reversals in
          24h
        </p>
        <table class="compact">
          <tbody>
            @for (t of ev.transactions; track t.id) {
              <tr [class.highlight]="duplicates().has(t.reference)">
                <td>
                  <code>{{ t.reference }}</code>
                </td>
                <td>{{ t.kind }}</td>
                <td>{{ t.amount }} {{ t.currency }}</td>
                <td>{{ t.merchant }}</td>
                <td>{{ t.occurred_at | date: 'short' }}</td>
                <td>{{ t.status }}</td>
              </tr>
            }
          </tbody>
        </table>
        @if (ev.duplicate_pairs.length) {
          <small class="muted">Highlighted: duplicate debits found by the system.</small>
        }
      }
    }

    @if (r.proposal; as p) {
      <h3>Proposed action</h3>
      <p>
        <strong>{{ actionLabel[p.action] }}</strong>
        @if (p.transaction_reference && p.amount) {
          · {{ p.amount }} for <code>{{ p.transaction_reference }}</code>
        }
      </p>
      <p class="muted">
        <small>{{ p.rationale }}</small>
      </p>
    }

    @if (r.decision; as d) {
      <h3>Policy decision</h3>
      <span class="badge" [attr.data-decision]="d.outcome">{{ outcomeLabel[d.outcome] }}</span>
      <ul class="reasons">
        @for (reason of d.reasons; track reason) {
          <li>{{ reason }}</li>
        }
      </ul>
    }

    @if (r.risk; as k) {
      <h3>Criticality (code)</h3>
      <p>
        <span class="badge" [attr.data-urgency]="k.priority"
          >{{ k.priority }} · score {{ k.score }}</span
        >
        @if (!k.signals.length) {
          <small class="muted">no risk signals</small>
        }
      </p>
      <ul class="reasons">
        @for (s of k.signals; track s.id) {
          <li>{{ s.id.replaceAll('_', ' ') }} (+{{ s.points }}): {{ s.detail }}</li>
        }
      </ul>
    }

    @if (r.approval; as a) {
      <h3>Approval</h3>
      <span
        class="badge"
        [attr.data-status]="
          a.status === 'approved' ? 'completed' : a.status === 'pending' ? 'queued' : 'failed'
        "
      >
        {{ a.status }}
      </span>
    }

    @if (r.execution; as x) {
      <h3>Execution</h3>
      <p>
        <span class="badge" [attr.data-status]="x.status === 'executed' ? 'completed' : 'failed'">{{
          x.status
        }}</span>
        {{ x.detail }}
        @if (x.verified) {
          <span class="badge" data-status="completed">verified in ledger</span>
        }
      </p>
    }

    @if (r.ticket; as t) {
      <h3>Ticket</h3>
      <p>
        <span class="badge" [attr.data-urgency]="t.priority">{{ t.code }} · {{ t.priority }}</span>
        <a routerLink="/tickets">open the ticket queue</a>
      </p>
      <ul class="reasons">
        @for (reason of t.reasons; track reason) {
          <li>{{ reason }}</li>
        }
      </ul>
    }

    @if (r.reply) {
      <h3>Draft reply to the customer</h3>
      <blockquote class="reply">{{ r.reply }}</blockquote>
    }
  `,
})
export class DecisionTrail {
  readonly result = input.required<WorkflowResult>();

  protected readonly duplicates = computed(
    () => new Set((this.result().evidence?.duplicate_pairs ?? []).flat()),
  );
  protected readonly actionLabel: Record<string, string> = {
    refund_transaction: 'Refund',
    escalate_to_human: 'Escalate to a human',
    no_action: 'No action (reply only)',
  };
  protected readonly outcomeLabel: Record<string, string> = {
    allow: 'allowed automatically',
    require_approval: 'needs approval',
    block: 'not automated',
  };
}
