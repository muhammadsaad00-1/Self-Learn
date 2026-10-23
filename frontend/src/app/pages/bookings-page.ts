import { DatePipe, DecimalPipe } from '@angular/common';
import { HttpClient, httpResource } from '@angular/common/http';
import { Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { firstValueFrom } from 'rxjs';

import { errorMessage } from '../core/errors';

interface TripOption {
  id: string;
  trip_ref: string;
  origin: string;
  destination: string;
  departs_at: string;
  fare: string;
  currency: string;
  seats_available: number;
}
interface Booking {
  id: string;
  trip_ref: string | null;
  customer_ref: string;
  booking_ref: string;
  seats: number;
  unit_fare: string;
  currency: string;
  status: string;
  expires_at: string | null;
  created_at: string;
}

@Component({
  selector: 'app-bookings-page',
  imports: [DatePipe, DecimalPipe, FormsModule],
  template: `
    <div class="page wide">
      <header class="page-header">
        <div>
          <h1>Seat bookings</h1>
          <p class="subtitle">
            Create a verified customer hold and track reservations. CNIC and phone are checked
            against the customer record; neither is shown or retained in bookings.
          </p>
        </div>
      </header>
      @if (error()) {
        <div class="callout" data-tone="danger" role="alert">{{ error() }}</div>
      }
      <section class="card pad stack">
        <h2>Book for a customer</h2>
        <div class="form-grid">
          <label
            >Trip<select [(ngModel)]="tripId" name="trip">
              <option value="">Select a trip</option>
              @for (t of trips.value() ?? []; track t.id) {
                <option [value]="t.id">
                  {{ t.trip_ref }} · {{ t.origin }} → {{ t.destination }} ·
                  {{ t.seats_available }} seats · {{ t.currency }} {{ t.fare }}
                </option>
              }
            </select></label
          >
          <label
            >Customer ID<input [(ngModel)]="customerRef" name="customerRef" autocomplete="off"
          /></label>
          <label
            >Phone number<input [(ngModel)]="phone" name="phone" inputmode="tel" autocomplete="off"
          /></label>
          <label
            >CNIC (13 digits)<input
              [(ngModel)]="cnic"
              name="cnic"
              inputmode="numeric"
              maxlength="20"
              autocomplete="off"
          /></label>
          <label
            >Seats<input [(ngModel)]="seats" name="seats" type="number" min="1" max="6"
          /></label>
        </div>
        <div>
          <button
            type="button"
            (click)="create()"
            [disabled]="busy() || !tripId || !customerRef || !phone || !cnic"
          >
            {{ busy() ? 'Saving…' : 'Verify and reserve' }}
          </button>
        </div>
        <small class="muted"
          >Holds expire after 20 minutes. Confirm payment through your existing channel before
          telling the customer the ticket is final.</small
        >
      </section>
      <section class="card" style="margin-top:20px">
        <div class="card-head">
          <h2>Recent reservations</h2>
          <button class="secondary" type="button" (click)="bookings.reload()">Refresh</button>
        </div>
        <div class="table-wrap">
          <table class="grid">
            <thead>
              <tr>
                <th>Booking</th>
                <th>Customer</th>
                <th>Trip</th>
                <th>Seats</th>
                <th>Status</th>
                <th>Created</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              @for (b of bookings.value() ?? []; track b.id) {
                <tr>
                  <td>{{ b.booking_ref }}</td>
                  <td>{{ b.customer_ref }}</td>
                  <td>{{ b.trip_ref ?? 'Trip removed' }}</td>
                  <td>
                    {{ b.seats }} · {{ b.currency }} {{ +b.unit_fare | number: '1.2-2' }} each
                  </td>
                  <td>
                    {{ b.status }}
                    @if (b.expires_at) {
                      · until {{ b.expires_at | date: 'shortTime' }}
                    }
                  </td>
                  <td>{{ b.created_at | date: 'short' }}</td>
                  <td>
                    @if (b.status === 'held') {
                      <button class="ghost" type="button" (click)="confirm(b)">Confirm</button>
                    }
                    @if (b.status === 'held' || b.status === 'confirmed') {
                      <button class="ghost" type="button" (click)="cancel(b)">Cancel</button>
                    }
                  </td>
                </tr>
              } @empty {
                <tr>
                  <td colspan="7" class="muted">No bookings yet.</td>
                </tr>
              }
            </tbody>
          </table>
        </div>
      </section>
    </div>
  `,
})
export class BookingsPage {
  private readonly http = inject(HttpClient);
  protected readonly trips = httpResource<TripOption[]>(() => '/v1/bookings/trips');
  protected readonly bookings = httpResource<Booking[]>(() => '/v1/bookings');
  protected readonly busy = signal(false);
  protected readonly error = signal<string | null>(null);
  protected tripId = '';
  protected customerRef = '';
  protected phone = '';
  protected cnic = '';
  protected seats = 1;

  protected async create(): Promise<void> {
    this.busy.set(true);
    this.error.set(null);
    try {
      await firstValueFrom(
        this.http.post('/v1/bookings', {
          trip_id: this.tripId,
          customer_ref: this.customerRef.trim(),
          phone: this.phone,
          cnic: this.cnic,
          seats: Number(this.seats),
          idempotency_key: crypto.randomUUID(),
        }),
      );
      this.cnic = '';
      this.phone = '';
      this.tripId = '';
      this.customerRef = '';
      this.bookings.reload();
      this.trips.reload();
    } catch (err) {
      this.error.set(errorMessage(err));
    } finally {
      this.busy.set(false);
    }
  }

  protected async cancel(booking: Booking): Promise<void> {
    this.error.set(null);
    try {
      await firstValueFrom(this.http.post(`/v1/bookings/${booking.id}/cancel`, {}));
      this.bookings.reload();
      this.trips.reload();
    } catch (err) {
      this.error.set(errorMessage(err));
    }
  }

  protected async confirm(booking: Booking): Promise<void> {
    this.error.set(null);
    try {
      await firstValueFrom(this.http.post(`/v1/bookings/${booking.id}/confirm`, {}));
      this.bookings.reload();
    } catch (err) {
      this.error.set(errorMessage(err));
    }
  }
}
