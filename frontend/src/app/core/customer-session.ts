import { Injectable, computed, signal } from '@angular/core';

const TOKEN_KEY = 'agentflow.customer-token';
const BANK_KEY = 'agentflow.customer-bank';

/**
 * The customer's session in the customer app: a customer token (from the OTP login or a
 * staff-issued link), kept in sessionStorage so it dies with the tab. Separate from the staff
 * session (`Auth`): the two never share credentials.
 */
@Injectable({ providedIn: 'root' })
export class CustomerSession {
  private readonly token = signal<string | null>(sessionStorage.getItem(TOKEN_KEY));
  /** The bank's name from the login step: shown at once, before the profile loads. */
  readonly bankName = signal<string | null>(sessionStorage.getItem(BANK_KEY));
  readonly loggedIn = computed(() => !!this.token());

  start(token: string, bankName?: string): void {
    sessionStorage.setItem(TOKEN_KEY, token);
    this.token.set(token);
    if (bankName) {
      sessionStorage.setItem(BANK_KEY, bankName);
      this.bankName.set(bankName);
    }
  }

  end(): void {
    sessionStorage.removeItem(TOKEN_KEY);
    sessionStorage.removeItem(BANK_KEY);
    this.token.set(null);
    this.bankName.set(null);
  }

  /** The bank (org id) the token belongs to, for the "sign in again" link. Read from the
   *  token's payload: display only, the server verifies the signature. */
  readonly bankId = computed(() => {
    try {
      const payload = this.token()?.split('.')[1] ?? '';
      return (
        (JSON.parse(atob(payload.replace(/-/g, '+').replace(/_/g, '/'))) as { org?: string }).org ??
        null
      );
    } catch {
      return null;
    }
  });

  headers(): Record<string, string> {
    return { Authorization: `Bearer ${this.token() ?? ''}` };
  }
}
