import { HttpClient, httpResource } from '@angular/common/http';
import { Component, DestroyRef, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { firstValueFrom } from 'rxjs';

import type {
  ImportBatch,
  ImportBatchDetail,
  ImportCommit,
  ImportField,
  ImportRow,
  ImportTemplate,
} from '../api/types';
import { Auth } from '../core/auth';
import { errorMessage } from '../core/errors';
import { FORMAT } from '../core/format';
import { Icon } from '../layout/icon';

const POLL_MS = 2000;

/**
 * Bring the company's records in: customers, transactions or trips, depending on the
 * industry. Typed, CSV/Excel, or a PDF/text report read by the extraction agent. Every row is
 * checked and reviewed by a person; only "Commit" writes to the tables the agents read.
 */
@Component({
  selector: 'app-data-page',
  imports: [FormsModule, Icon, ...FORMAT],
  template: `
    <div class="page wide">
      <header class="page-header">
        <div>
          <h1>Data</h1>
          <p class="subtitle">
            Bring your records in by hand, from CSV or Excel, or from a PDF report. Nothing reaches
            the agents until a person has reviewed it and committed it.
          </p>
        </div>
        @if (open()) {
          <div class="actions">
            <button type="button" class="secondary" (click)="close()">
              <app-icon name="chevron-left" /> All imports
            </button>
          </div>
        }
      </header>

      @if (error()) {
        <div class="callout" data-tone="danger" role="alert" style="margin-bottom: 16px">
          {{ error() }}
        </div>
      }

      @if (open(); as b) {
        <section class="card">
          <div class="card-head" style="align-items: flex-start">
            <div class="grow stack-sm">
              <div class="row">
                <h2>{{ templateOf(b.record_type)?.label ?? b.record_type }}</h2>
                <span class="badge dot" [attr.data-tone]="statusTone(b.status)">{{
                  statusLabel[b.status]
                }}</span>
                <small class="muted">
                  {{ b.filename ?? (b.source | human) }} · {{ b.created_at | ago }}
                </small>
              </div>
              @for (n of b.notes; track n) {
                <small class="muted"><app-icon name="alert" [size]="12" /> {{ n }}</small>
              }
            </div>
          </div>

          @if (b.status === 'extracting') {
            <div class="card-body stack-sm">
              <div class="skeleton" style="width: 60%"></div>
              <div class="skeleton" style="width: 85%"></div>
              <small class="muted"
                ><app-icon name="sparkles" [size]="12" /> The extraction agent is reading the
                report… rows appear here for review.</small
              >
            </div>
          } @else if (b.status === 'failed') {
            <div class="card-body">
              <div class="callout" data-tone="danger">
                Couldn't read the report: {{ b.error }}. Try a CSV, or add the rows by hand.
              </div>
            </div>
          } @else {
            @if (b.status === 'committed' && b.summary; as s) {
              <div class="card-body">
                <div class="callout" data-tone="brand">
                  <app-icon name="check-circle" />
                  <span
                    >Committed: {{ s['created'] }} added, {{ s['updated'] }} updated,
                    {{ s['skipped_duplicates'] }} already there (skipped). The agents use them
                    now.</span
                  >
                </div>
              </div>
            }
            <div class="table-wrap">
              <table class="grid">
                <thead>
                  <tr>
                    <th style="width: 40px">#</th>
                    @for (f of fields(); track f.name) {
                      <th [title]="f.hint">{{ f.label }}{{ f.required ? ' *' : '' }}</th>
                    }
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  @for (r of b.rows; track r.id) {
                    <tr [class.has-errors]="hasErrors(r)">
                      <td class="muted">{{ r.position }}</td>
                      @for (f of fields(); track f.name) {
                        <td [class.bad]="r.errors[f.name]">
                          @if (editable()) {
                            @if (f.choices.length || f.kind === 'boolean') {
                              <select
                                [ngModel]="cell(r, f)"
                                (ngModelChange)="edit(r, f, $event)"
                                [attr.aria-label]="f.label + ' row ' + r.position"
                              >
                                <option value=""></option>
                                @for (c of optionsOf(f); track c) {
                                  <option [value]="c">{{ c | human }}</option>
                                }
                              </select>
                            } @else {
                              <input
                                [ngModel]="cell(r, f)"
                                (change)="edit(r, f, $any($event.target).value)"
                                [attr.aria-label]="f.label + ' row ' + r.position"
                              />
                            }
                          } @else {
                            {{ cell(r, f) }}
                          }
                          @if (r.errors[f.name]; as problem) {
                            <small class="error cell-error">{{ problem }}</small>
                          }
                        </td>
                      }
                      <td>
                        @if (editable()) {
                          <button
                            type="button"
                            class="ghost sm icon"
                            (click)="remove(r)"
                            [attr.aria-label]="'Delete row ' + r.position"
                          >
                            <app-icon name="x" />
                          </button>
                        }
                      </td>
                    </tr>
                  } @empty {
                    <tr>
                      <td [attr.colspan]="fields().length + 2">
                        <div class="empty">
                          <strong>No rows yet</strong><span>Add rows by hand below.</span>
                        </div>
                      </td>
                    </tr>
                  }
                </tbody>
              </table>
            </div>
            @if (editable()) {
              <div class="card-body row" style="border-top: 1px solid var(--border)">
                <button type="button" class="secondary" (click)="addRow()" [disabled]="busy()">
                  <app-icon name="plus" /> Add row
                </button>
                <span class="muted">
                  {{ b.row_count }} rows ·
                  @if (b.error_count) {
                    <span class="error">{{ b.error_count }} with problems: fix or delete them</span>
                  } @else {
                    all checked
                  }
                </span>
                <span class="spacer"></span>
                <button type="button" class="ghost" (click)="discard()" [disabled]="busy()">
                  Discard
                </button>
                <button
                  type="button"
                  (click)="commit()"
                  [disabled]="busy() || b.error_count > 0 || b.row_count === 0"
                  title="Write these rows to your records"
                >
                  <app-icon name="check" /> Commit {{ b.row_count }} rows
                </button>
              </div>
            }
          }
        </section>
      } @else {
        <div class="type-grid">
          @for (t of templates.value() ?? []; track t.name) {
            <section class="card pad stack">
              <div class="row" style="flex-wrap: nowrap">
                <span class="avatar square"><app-icon [name]="iconOf(t.name)" /></span>
                <div class="grow">
                  <h2>{{ t.label }}</h2>
                  <small class="muted">{{ t.description }}</small>
                </div>
              </div>
              <small class="faint"> Fields: {{ requiredOf(t) }} </small>
              <div class="row">
                <label class="button secondary" style="cursor: pointer">
                  <app-icon name="upload" /> CSV, Excel or report
                  <input
                    class="sr-only"
                    type="file"
                    accept=".csv,.tsv,.xlsx,.pdf,.txt,.md"
                    (change)="upload(t, $any($event.target))"
                  />
                </label>
                <button type="button" class="secondary" (click)="manual(t)" [disabled]="busy()">
                  <app-icon name="plus" /> Add by hand
                </button>
                <button type="button" class="ghost" (click)="sample(t)" [disabled]="busy()">
                  Try a sample
                </button>
              </div>
            </section>
          }
        </div>

        <section class="card" style="margin-top: 16px">
          <div class="card-head"><h2 class="grow">Recent imports</h2></div>
          <div class="table-wrap">
            <table>
              <tbody>
                @for (b of history.value() ?? []; track b.id) {
                  <tr class="clickable" (click)="openBatch(b.id)">
                    <td>
                      <strong>{{ templateOf(b.record_type)?.label ?? b.record_type }}</strong>
                      <div>
                        <small class="muted">{{ b.filename ?? (b.source | human) }}</small>
                      </div>
                    </td>
                    <td>
                      <span class="badge dot" [attr.data-tone]="statusTone(b.status)">{{
                        statusLabel[b.status]
                      }}</span>
                    </td>
                    <td>
                      @if (b.summary; as s) {
                        <small class="muted"
                          >{{ s['created'] }} added · {{ s['updated'] }} updated ·
                          {{ s['skipped_duplicates'] }} skipped</small
                        >
                      }
                    </td>
                    <td style="text-align: right">
                      <small class="faint">{{ b.created_at | ago }}</small>
                    </td>
                  </tr>
                } @empty {
                  <tr>
                    <td>
                      <div class="empty">
                        <strong>No imports yet</strong>
                        <span>Start with your customers, then their records.</span>
                      </div>
                    </td>
                  </tr>
                }
              </tbody>
            </table>
          </div>
        </section>
      }
    </div>
  `,
})
export class DataPage {
  private readonly http = inject(HttpClient);
  private readonly auth = inject(Auth);

  protected readonly busy = signal(false);
  protected readonly error = signal<string | null>(null);
  protected readonly open = signal<ImportBatchDetail | null>(null);
  protected readonly statusLabel: Record<ImportBatch['status'], string> = {
    extracting: 'Reading report',
    review: 'Needs review',
    committed: 'Committed',
    discarded: 'Discarded',
    failed: 'Failed',
  };

  protected readonly templates = httpResource<ImportTemplate[]>(() =>
    this.auth.orgId() ? '/v1/imports/templates' : undefined,
  );
  protected readonly history = httpResource<ImportBatch[]>(() =>
    this.auth.orgId() ? '/v1/imports' : undefined,
  );

  protected readonly fields = computed(() => {
    const b = this.open();
    return b ? (this.templateOf(b.record_type)?.fields ?? []) : [];
  });
  protected readonly editable = computed(() => this.open()?.status === 'review');

  constructor() {
    // While a report is being read, poll until the rows arrive.
    const timer = setInterval(() => {
      const b = this.open();
      if (b?.status === 'extracting') void this.openBatch(b.id);
    }, POLL_MS);
    inject(DestroyRef).onDestroy(() => clearInterval(timer));
  }

  protected templateOf(name: string): ImportTemplate | undefined {
    return this.templates.value()?.find((t) => t.name === name);
  }

  protected requiredOf(t: ImportTemplate): string {
    return t.fields.map((f) => f.label + (f.required ? '*' : '')).join(', ');
  }

  protected iconOf(name: string): string {
    return { customers: 'users', transactions: 'money', trips: 'clock' }[name] ?? 'file';
  }

  protected statusTone(status: ImportBatch['status']): string {
    return (
      { review: 'warning', committed: 'success', failed: 'danger', extracting: 'info' }[
        status as string
      ] ?? 'neutral'
    );
  }

  protected optionsOf(f: ImportField): string[] {
    return f.kind === 'boolean' ? ['true', 'false'] : f.choices;
  }

  protected hasErrors(r: ImportRow): boolean {
    return Object.keys(r.errors).length > 0;
  }

  protected cell(r: ImportRow, f: ImportField): string {
    const v = r.data[f.name];
    if (v === null || v === undefined) return '';
    // Checked times are ISO in the company's zone ("2026-10-24T17:00:00+05:00"): show the
    // local "2026-10-24 17:00" people typed, not the machine format.
    if (f.kind === 'datetime' && typeof v === 'string' && /^\d{4}-\d\d-\d\dT/.test(v)) {
      return v.slice(0, 16).replace('T', ' ');
    }
    return String(v);
  }

  protected close(): void {
    this.open.set(null);
    this.history.reload();
  }

  protected async openBatch(id: string): Promise<void> {
    await this.run(async () => {
      this.open.set(await firstValueFrom(this.http.get<ImportBatchDetail>(`/v1/imports/${id}`)));
    });
  }

  protected async manual(t: ImportTemplate): Promise<void> {
    await this.run(async () => {
      this.open.set(
        await firstValueFrom(
          this.http.post<ImportBatchDetail>('/v1/imports/manual', {
            record_type: t.name,
            rows: [{}],
          }),
        ),
      );
    });
  }

  protected async sample(t: ImportTemplate): Promise<void> {
    await this.run(async () => {
      this.open.set(
        await firstValueFrom(
          this.http.post<ImportBatchDetail>('/v1/imports/sample', { record_type: t.name }),
        ),
      );
    });
  }

  protected async upload(t: ImportTemplate, input: HTMLInputElement): Promise<void> {
    const file = input.files?.[0];
    if (!file) return;
    const form = new FormData();
    form.append('record_type', t.name);
    form.append('file', file);
    await this.run(async () => {
      this.open.set(
        await firstValueFrom(this.http.post<ImportBatchDetail>('/v1/imports/file', form)),
      );
    });
    input.value = '';
  }

  /** One cell changed: the row is checked again server-side. */
  protected async edit(r: ImportRow, f: ImportField, value: string): Promise<void> {
    const b = this.open();
    if (!b) return;
    await this.run(async () => {
      const updated = await firstValueFrom(
        this.http.patch<ImportRow>(`/v1/imports/${b.id}/rows/${r.id}`, {
          data: { ...r.data, [f.name]: value },
        }),
      );
      const rows = b.rows.map((x) => (x.id === r.id ? updated : x));
      this.open.set({ ...b, rows, error_count: rows.filter((x) => this.hasErrors(x)).length });
    });
  }

  protected async remove(r: ImportRow): Promise<void> {
    const b = this.open();
    if (!b) return;
    await this.run(async () => {
      await firstValueFrom(this.http.delete(`/v1/imports/${b.id}/rows/${r.id}`));
      const rows = b.rows.filter((x) => x.id !== r.id);
      this.open.set({
        ...b,
        rows,
        row_count: rows.length,
        error_count: rows.filter((x) => this.hasErrors(x)).length,
      });
    });
  }

  protected async addRow(): Promise<void> {
    const b = this.open();
    if (!b) return;
    await this.run(async () => {
      this.open.set(
        await firstValueFrom(
          this.http.post<ImportBatchDetail>(`/v1/imports/${b.id}/rows`, { data: {} }),
        ),
      );
    });
  }

  protected async commit(): Promise<void> {
    const b = this.open();
    if (!b) return;
    await this.run(async () => {
      const result = await firstValueFrom(
        this.http.post<ImportCommit>(`/v1/imports/${b.id}/commit`, {}),
      );
      this.open.set(await firstValueFrom(this.http.get<ImportBatchDetail>(`/v1/imports/${b.id}`)));
      if (!result.committed) {
        this.error.set(`${result.blocked} rows have problems: fix or delete them first.`);
      }
    });
  }

  protected async discard(): Promise<void> {
    const b = this.open();
    if (!b) return;
    await this.run(async () => {
      await firstValueFrom(this.http.post(`/v1/imports/${b.id}/discard`, {}));
      this.close();
    });
  }

  private async run(action: () => Promise<void>): Promise<void> {
    this.busy.set(true);
    this.error.set(null);
    try {
      await action(); // may set its own error at the end
    } catch (err) {
      this.error.set(errorMessage(err));
    } finally {
      this.busy.set(false);
    }
  }
}
