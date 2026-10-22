import { Pipe, PipeTransform } from '@angular/core';

/** Words people use for our enum values; anything else falls back to "Sentence case". */
const LABELS: Record<string, string> = {
  duplicate_charge: 'Duplicate charge',
  failed_payment: 'Failed transfer',
  refund_request: 'Refund request',
  unauthorized_transaction: 'Suspected fraud',
  card_or_account_blocked: 'Card or account blocked',
  kyc_document: 'Documents',
  account_access: 'Account access',
  general_inquiry: 'Question',
  other: 'Other',
  with_specialist: 'With a specialist',
  in_review: 'Awaiting approval',
  waiting_approval: 'Awaiting approval',
  in_progress: 'In progress',
  refund_transaction: 'Refund',
  freeze_card: 'Freeze card',
  answer_from_records: 'Answer from records',
  escalate_to_human: 'Escalate to a human',
  no_action: 'Reply only',
  require_approval: 'Needs approval',
  allow: 'Allowed',
  block: 'Blocked',
};

export function humanize(value: string | null | undefined): string {
  if (!value) return '';
  if (LABELS[value]) return LABELS[value];
  const words = value.replaceAll('_', ' ').replaceAll('.', ' · ');
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/** "just now", "5 min ago", "3 h ago", "yesterday", "Oct 12". */
export function timeAgo(value: string | Date | null | undefined, now = Date.now()): string {
  if (!value) return '';
  const date = new Date(value);
  const seconds = Math.round((now - date.getTime()) / 1000);
  if (seconds < 45) return 'just now';
  if (seconds < 3600) return `${Math.round(seconds / 60)} min ago`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)} h ago`;
  if (seconds < 2 * 86400) return 'yesterday';
  return date.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

/** "Sara Khan" -> "SK", "Ayesha (Ops lead)" -> "AO": letters only, never punctuation. */
export function initialsOf(name: string | null | undefined): string {
  const words = (name ?? '')
    .replace(/[^\p{L}\p{N}\s]/gu, ' ')
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2);
  return words.map((w) => w[0]!.toUpperCase()).join('') || '?';
}

/** Tone for a badge: success / warning / danger / info / neutral. */
export function toneOf(value: string | null | undefined): string {
  switch (value) {
    case 'completed':
    case 'resolved':
    case 'approved':
    case 'executed':
    case 'ready':
    case 'allow':
      return 'success';
    case 'critical':
    case 'failed':
    case 'rejected':
    case 'block':
    case 'not_executed':
    case 'expired':
      return 'danger';
    case 'high':
    case 'queued':
    case 'running':
    case 'pending':
    case 'processing':
    case 'waiting_approval':
    case 'in_review':
    case 'with_specialist':
    case 'require_approval':
      return 'warning';
    case 'open':
    case 'in_progress':
    case 'normal':
      return 'info';
    default:
      return 'neutral';
  }
}

@Pipe({ name: 'human' })
export class HumanPipe implements PipeTransform {
  transform(value: string | null | undefined): string {
    return humanize(value);
  }
}

@Pipe({ name: 'ago' })
export class AgoPipe implements PipeTransform {
  transform(value: string | Date | null | undefined): string {
    return timeAgo(value);
  }
}

@Pipe({ name: 'tone' })
export class TonePipe implements PipeTransform {
  transform(value: string | null | undefined): string {
    return toneOf(value);
  }
}

@Pipe({ name: 'initials' })
export class InitialsPipe implements PipeTransform {
  transform(value: string | null | undefined): string {
    return initialsOf(value);
  }
}

/** Everything a page needs to show values the way people read them. */
export const FORMAT = [HumanPipe, AgoPipe, TonePipe, InitialsPipe] as const;
