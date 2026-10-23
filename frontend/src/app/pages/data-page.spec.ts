import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';

import type { ImportBatchDetail, ImportTemplate } from '../api/types';
import { Auth } from '../core/auth';
import { DataPage } from './data-page';

const TRIPS: ImportTemplate = {
  name: 'trips',
  label: 'Trips',
  description: 'Your schedule',
  fields: [
    { name: 'trip_ref', label: 'Trip ID', kind: 'text', required: true, choices: [], hint: '' },
    {
      name: 'seats_available',
      label: 'Seats available',
      kind: 'integer',
      required: true,
      choices: [],
      hint: '',
    },
  ],
};
const BATCH: ImportBatchDetail = {
  id: 'b1',
  record_type: 'trips',
  source: 'sample',
  filename: 'sample-trips.csv',
  status: 'review',
  notes: [],
  error: null,
  summary: null,
  created_at: '2026-10-23T10:00:00Z',
  committed_at: null,
  row_count: 1,
  error_count: 1,
  rows: [
    {
      id: 'r1',
      position: 1,
      data: { trip_ref: 'LHE-MUX', seats_available: 'twelve' },
      errors: { seats_available: 'not a number' },
    },
  ],
};

describe('DataPage', () => {
  const settle = async () => {
    TestBed.tick();
    await new Promise((r) => setTimeout(r));
    TestBed.tick();
  };

  it('reviews a sample, fixes the bad cell, then commits', async () => {
    TestBed.configureTestingModule({
      providers: [provideRouter([]), provideHttpClient(), provideHttpClientTesting()],
    });
    vi.spyOn(TestBed.inject(Auth), 'orgId').mockReturnValue('org-a');
    const backend = TestBed.inject(HttpTestingController);
    const fixture = TestBed.createComponent(DataPage);
    await settle();
    backend.expectOne('/v1/imports/templates').flush([TRIPS]);
    backend.expectOne('/v1/imports').flush([]);
    await settle();
    const el = fixture.nativeElement as HTMLElement;

    [...el.querySelectorAll('button')]
      .find((b) => b.textContent?.includes('Try a sample'))!
      .click();
    await settle();
    const sample = backend.expectOne('/v1/imports/sample');
    expect(sample.request.body).toEqual({ record_type: 'trips' });
    sample.flush(BATCH);
    await settle();
    expect(el.textContent).toContain('not a number');
    const commit = [...el.querySelectorAll('button')].find((b) =>
      b.textContent?.includes('Commit'),
    )!;
    expect(commit.disabled).toBe(true); // problems first

    const seats = el.querySelector<HTMLInputElement>('input[aria-label="Seats available row 1"]')!;
    seats.value = '12';
    seats.dispatchEvent(new Event('change'));
    await settle();
    const patch = backend.expectOne('/v1/imports/b1/rows/r1');
    expect(patch.request.body).toEqual({ data: { trip_ref: 'LHE-MUX', seats_available: '12' } });
    patch.flush({
      id: 'r1',
      position: 1,
      data: { trip_ref: 'LHE-MUX', seats_available: 12 },
      errors: {},
    });
    await settle();
    expect(commit.disabled).toBe(false);

    commit.click();
    await settle();
    backend.expectOne('/v1/imports/b1/commit').flush({
      committed: true,
      blocked: 0,
      created: 1,
      updated: 0,
      skipped_duplicates: 0,
    });
    await settle();
    backend.expectOne('/v1/imports/b1').flush({
      ...BATCH,
      status: 'committed',
      error_count: 0,
      summary: { created: 1, updated: 0, skipped_duplicates: 0 },
      rows: [
        { id: 'r1', position: 1, data: { trip_ref: 'LHE-MUX', seats_available: 12 }, errors: {} },
      ],
    });
    await settle();
    expect(el.textContent).toContain('Committed: 1 added');
  });
});
