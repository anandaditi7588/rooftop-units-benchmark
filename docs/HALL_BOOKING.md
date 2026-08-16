# Silicon Bay Society — Hall &amp; Amenity Booking Form

An online form that captures everything the society's *"Rule and Regulation —
For personal use of Society's amenities by Resident"* document (15/10/2024)
asks a resident to agree to, checks that nobody else already holds the slot,
and notifies the society office by **email** and **WhatsApp** the moment a
resident submits.

A resident scans the QR code at the gate → picks **Book a hall or lawn** → fills
the form → the office is notified and confirms with one tap in the email.

Everything about email, WhatsApp, UPI payment, hosting, the QR code and the
Google Sheet archive is **shared with the trainer registration form** and is
documented once, in [docs/GYM_FORM.md](GYM_FORM.md). This file covers what is
specific to bookings.

---

## 1. What it captures

Straight from the rules document:

| Amenity | Where | Max persons | Charges |
|---|---|---|---|
| Conference Hall | Below D Building | 75 | 4 hours ₹2,500 · 8 hours ₹3,500 |
| Party Lawn | At the swimming pool | 75 | 4 hours ₹3,000 · 8 hours ₹4,000 |
| Community Hall | A &amp; B Buildings | 30 | ₹2,000 for a day |

- **Resident:** name, flat number, mobile, WhatsApp, email.
- **Booking:** amenity, duration, date, start time, occasion, expected persons.
- **Charges:** shown live as the resident chooses, alongside the refundable
  **₹5,000 security deposit** payable by cheque.
- **All 11 rules**, each with its own tick box. All are required.
- **Declaration:** typed signature and place.

The form enforces the document's own limits, on the server as well as in the
browser:

- Bookings run **8:30 am to 10:00 pm** — a four-hour slot cannot start after
  6:00 pm, and an eight-hour slot cannot start after 2:00 pm.
- The Community Hall is priced per day, so it simply takes the whole window.
- Capacity is per venue: 75 for the hall and lawn, 30 for the community hall.
- **Today cannot be booked.** Rule 3 collects the cash one day before the
  function, so the earliest bookable date is tomorrow.

---

## 2. Double booking — how a slot is held

> *"if someone book the hall and another user wants to book on same time and
> date then you have show already booked by xyz person"*

When a resident submits, the form checks every stored booking for the same
amenity on the same date. If the times overlap, the submission is refused and
the resident is told **who holds it**:

> Party Lawn (At the swimming pool) is already booked by Rahul Patil (Flat
> A-304) on 25 Aug 2026 from 6:00 pm to 10:00 pm. Please choose another time or
> date.

Four things are worth knowing about how this behaves:

- **A pending booking still holds the slot.** The first resident asked first;
  the office taking a day to decide must not cost them their date. The message
  says "already requested" rather than "already booked" so the second resident
  knows it is not yet final.
- **A rejected booking releases it.** The moment the office rejects a request,
  that time is bookable again.
- **Back-to-back bookings do not clash.** A function ending at 1 pm and one
  starting at 1 pm share only an instant — which is how a hall actually turns
  over. Treating that as a clash would cost the society an entire slot.
- **Two people submitting at the same second cannot both win.** The check and
  the write happen together under a lock, so the second submission sees the
  first.

The resident does not have to submit to find out. `/hall/availability` is
checked from the browser as they choose the amenity, date and time, so a taken
slot shows up while they are still filling the form. `/hall/calendar` lists
every upcoming booking publicly — the same information a notice board would
carry — so they can pick a free date before they start.

---

## 3. Pages

On the deployed service these sit under `/hall`.

| Page | Who it is for | Password? |
|---|---|---|
| `/hall/` | The booking form | Public |
| `/hall/rules` | The full rulebook and charges | Public |
| `/hall/calendar` | Which dates are already taken | Public |
| `/hall/availability` | JSON check used by the form as you type | Public |
| `/hall/submitted/<ref>` | Confirmation page after submitting | Public |
| `/hall/qr.png` | QR straight to the booking form | Public |
| `/hall/decide/<ref>` | Confirm / reject, from the office email | Signed link |
| `/hall/admin` | All bookings received | Office only |
| `/hall/admin/bookings.csv` | Same data as a spreadsheet | Office only |
| `/hall/admin/sheet-sync` | Push every booking to the Sheet (POST) | Office only |

---

## 4. What happens after a resident submits

1. The booking is written to disk **first** — a delivery failure can never lose
   it.
2. The office gets an email with the full booking and two buttons, **Approve
   booking** and **Reject**. Each link carries an HMAC bound to that booking and
   that decision, so it cannot be edited into a different verdict, and clicking
   only opens a confirmation page — mail scanners follow links before a person
   sees them.
3. The office also gets a short WhatsApp message (or a one-tap `wa.me` link in
   the email if no WhatsApp provider is configured).
4. The resident gets their own copy by email, with the reminders that matter:
   cash the day before, ₹5,000 deposit by cheque, chairs, tables and parking
   are theirs to arrange, clean up afterwards, lights and fans off.
5. When the office decides, the resident is told by **email and WhatsApp** —
   confirmed, or not confirmed with the office's note.
6. Every step is mirrored into the Google Sheet, if one is connected
   (see [§6b of the trainer guide](GYM_FORM.md#6b-the-google-sheet-history-free-10-minutes)).

---

## 5. Paying online (optional)

The rules ask for cash one day before the function, and that remains the
default. If `GYM_UPI_ID` is set, the confirmation page also offers a UPI button
and QR for the booking charge — the same mechanism as the trainer form.

UPI gives the server no callback, so nothing here proves money moved. The
resident enters the reference from their payment app, the office matches it
against the society account, and every surface says **reported**, not paid. The
₹5,000 deposit is by cheque either way.

---

## 6. Where bookings are stored

- `output/gym_form/bookings.jsonl` — the complete record, one line each.
- `output/gym_form/bookings.csv` — the same data for Excel.
- `output/gym_form/booking_payments.jsonl` — reported payments.
- `output/gym_form/booking_approvals.jsonl` — office decisions.

Payments and decisions live in their own append-only files, so the booking the
resident signed is never rewritten and the decision history stays auditable.

> **On free hosting the disk is wiped on every redeploy.** Connect the Google
> Sheet — that is the durable history.

---

## 7. Editing the rules, venues or charges later

Everything the document specifies lives in `hallform/rules.py` as data: the
venues, their locations, capacities and per-slot charges, the list of occasions,
the booking window, the deposit, and all 11 rules with the wording of each
acknowledgement.

Change a charge there and it updates the form, the rules page, the confirmation
page, both emails, the WhatsApp message and the CSV at once. No template needs
touching. `tests/test_hall_form.py` asserts the charges and capacities against
the document, so a typo shows up as a failing test rather than a wrong invoice.

---

## 8. Running and testing

```bash
uvicorn gymform.standalone:app --host 0.0.0.0 --port 8000
# chooser: http://localhost:8000/
# booking: http://localhost:8000/hall/

pytest tests/test_hall_form.py tests/test_portal.py
```
