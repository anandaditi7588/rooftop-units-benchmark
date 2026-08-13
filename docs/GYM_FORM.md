# Silicon Bay Society — Personal Trainer Gym Registration Form

An online form that captures everything the society's *"Society Gym Rules for
Personal Trainers"* document asks a trainer to submit, records their
acknowledgement of all 15 rules, and notifies the society office by **email**
and **WhatsApp** the moment a trainer submits.

A trainer scans a QR code at the gym door → the form opens on their phone →
they fill it in and submit → the office is notified.

- **Notification email:** `dulange111@gmail.com`
- **Notification WhatsApp:** `+91 7588610829`

Both are defaults in code and can be changed with environment variables (below)
without touching any Python.

---

## 1. Pages

Mounted at `/gym` on the main application (`https://<your-host>/gym`).

| Page | Who it is for | Password? |
|---|---|---|
| `/gym/` | The registration form itself | Public |
| `/gym/rules` | The full rulebook, readable on a phone | Public |
| `/gym/poster` | **Printable A4 notice with the QR code** | Public |
| `/gym/qr.png` | The QR code as a PNG (`?size=4…20`) | Public |
| `/gym/submitted/<ref>` | Confirmation page after submitting | Public |
| `/gym/admin` | All registrations received | Office only |
| `/gym/admin/submissions.csv` | Same data as a spreadsheet | Office only |
| `/gym/admin/diagnostics` | Which delivery channels are live | Office only |
| `/gym/admin/test-notification` | Sends a dummy registration (POST) | Office only |

The form is public on purpose — a trainer at the gym gate cannot be expected to
hold a password. The review pages are separately protected and **fail closed**:
until you set an admin login they return 503 rather than serve trainers'
ID numbers and addresses to anyone who finds the URL.

---

## 2. What the form captures

Straight from the rules document:

- **Rule 1 — Registration:** name, mobile, WhatsApp, email, government ID
  (Aadhar / PAN / Driving License) with number and an optional photo or PDF
  upload, residential address, optional emergency contact.
- **Rule 1 — Client list:** a repeatable row per client with **client name,
  flat number and training slot** (days + start/end time), exactly the three
  columns the document asks for.
- **Rule 1 — Amenity fee:** calculated live from the client count and shown to
  the trainer before they submit — up to 4 clients ₹1,000/month, 5–9 clients
  ₹2,000/month, 10 or more ₹3,000/month.
- **Rule 2 — Timings:** any slot outside 6–11 am / 4–9 pm is flagged as the
  trainer types, and cannot be submitted until they confirm they have informed
  the society office *and* the security team.
- **Rules 4 & 14 — Trainee limit:** the form works out how many clients overlap
  at any one moment on any one day. More than four at a time is blocked unless
  the trainer enters the committee's written approval reference. Five clients in
  *staggered* slots is fine — the limit is four at a time, not four in total.
- **Rules 1–15 — Acknowledgement:** every rule is shown in full with its own
  tick box. All 15 are required.
- **Declaration:** typed signature and place, matching the signature block at
  the end of the document.

Everything is validated again on the server, so the rules hold even if a browser
has JavaScript switched off.

---

## 3. Turning on email notifications

Email uses plain SMTP. With a Gmail account:

1. Turn on 2-Step Verification on the Google account that will *send* the mail.
2. Create an **App Password**: Google Account → Security → 2-Step Verification →
   App passwords. You get a 16-character password.
3. Set these environment variables on the server:

   ```bash
   GYM_SMTP_USER=the.sending.account@gmail.com
   GYM_SMTP_PASSWORD=abcdefghijklmnop     # the 16-character App Password
   GYM_NOTIFY_EMAIL=dulange111@gmail.com  # who receives registrations
   ```

> Your normal Gmail password will **not** work — Google blocks it for SMTP. If
> the login is rejected, the confirmation page says so in plain words rather
> than failing silently.

The office receives a formatted summary with the client list, the calculated
fee, any rule warnings, and the uploaded ID proof attached. The trainer
receives their own confirmation with their reference number and what they
agreed to (switch off with `GYM_SEND_TRAINER_COPY=0`).

Check it works without waiting for a real trainer:

```bash
curl -u office:yourpassword -X POST https://<your-host>/gym/admin/test-notification
```

---

## 4. Turning on WhatsApp notifications

WhatsApp does not allow a server to message a number without going through an
approved business API. The form supports both providers, and works usefully
without either.

### Option A — nothing configured (works out of the box)

The notification email carries a **"Forward this to WhatsApp"** button, and the
trainer's confirmation page offers the same one-tap link. Tapping it opens
WhatsApp with the full registration summary already typed, addressed to
+91 7588610829. Delivery is one tap, but a human has to make that tap.

### Option B — Twilio (quickest automatic route)

1. Create a Twilio account and open **Messaging → Try it out → WhatsApp sandbox**.
2. Send the sandbox join code from the +91 7588610829 phone once, so that number
   is allowed to receive sandbox messages.
3. Set:

   ```bash
   GYM_TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
   GYM_TWILIO_AUTH_TOKEN=your-auth-token
   GYM_TWILIO_WHATSAPP_FROM=whatsapp:+14155238886   # sandbox sender
   ```

The sandbox is fine for a society. For a permanent sender that never needs
re-joining, apply for your own approved WhatsApp number in Twilio and put it in
`GYM_TWILIO_WHATSAPP_FROM`.

### Option C — WhatsApp Cloud API (direct from Meta)

```bash
GYM_META_PHONE_NUMBER_ID=123456789012345
GYM_META_ACCESS_TOKEN=EAAG...
```

One caveat worth knowing: Meta only delivers free-form text inside a 24-hour
window after the recipient last messaged your business number. Outside that
window an approved message template is required, and the Cloud API will reject
the send — the form reports that rejection on the confirmation page instead of
pretending it worked.

---

## 5. The QR code

Open **`https://<your-host>/gym/poster`** and print it (Ctrl/Cmd + P). It is an
A4 notice — "Personal Trainers Register Here" — with the QR code, the four
steps, the gym hours and the fee slabs. Put it up at the gym door and the
security desk. Trainers point their phone camera at it and the form opens.

The QR encodes the form's public URL, worked out from the incoming request. If
your host sits behind a proxy or custom domain and the encoded URL comes out
wrong, pin it:

```bash
GYM_PUBLIC_URL=https://silicon-bay.example.com/gym
```

The raw image is at `/gym/qr.png` (`?size=16` for a larger print).

---

## 6. Where submissions are stored

- `output/gym_form/submissions.jsonl` — the complete record, one line each.
- `output/gym_form/submissions.csv` — the same data for Excel.
- `output/gym_form/id_proofs/` — uploaded ID files, named by reference.

Storage is written **before** any notification is attempted, so a wrong SMTP
password can never lose a registration.

> **On free hosting tiers (Render, Railway) the disk is wiped on every redeploy.**
> Treat the notification email — which carries the full summary and the ID proof
> attachment — as the durable copy, and download the CSV periodically from
> `/gym/admin/submissions.csv`.

---

## 7. Environment variables

| Variable | Default | What it does |
|---|---|---|
| `GYM_NOTIFY_EMAIL` | `dulange111@gmail.com` | Who receives registrations |
| `GYM_NOTIFY_WHATSAPP` | `917588610829` | WhatsApp recipient, country code first |
| `GYM_SEND_TRAINER_COPY` | `1` | Send the trainer their own confirmation |
| `GYM_SMTP_HOST` | `smtp.gmail.com` | SMTP server |
| `GYM_SMTP_PORT` | `587` | SMTP port |
| `GYM_SMTP_USER` | — | SMTP login (email sending is off until set) |
| `GYM_SMTP_PASSWORD` | — | SMTP password / Gmail App Password |
| `GYM_SMTP_FROM` | `GYM_SMTP_USER` | From address, if different |
| `GYM_SMTP_USE_SSL` | `0` | `1` for implicit SSL (port 465) instead of STARTTLS |
| `GYM_TWILIO_ACCOUNT_SID` | — | Twilio SID (enables Twilio WhatsApp) |
| `GYM_TWILIO_AUTH_TOKEN` | — | Twilio auth token |
| `GYM_TWILIO_WHATSAPP_FROM` | sandbox number | Approved WhatsApp sender |
| `GYM_META_PHONE_NUMBER_ID` | — | WhatsApp Cloud API sender ID |
| `GYM_META_ACCESS_TOKEN` | — | WhatsApp Cloud API token |
| `GYM_ADMIN_USERNAME` | falls back to `RTU_AUTH_USERNAME` | Login for the review pages |
| `GYM_ADMIN_PASSWORD` | falls back to `RTU_AUTH_PASSWORD` | Password for the review pages |
| `GYM_PUBLIC_URL` | derived from the request | Pin the URL the QR encodes |
| `GYM_SUBMIT_COOLDOWN` | `20` | Seconds between submissions from one IP |
| `GYM_MAX_ID_PROOF_BYTES` | `5242880` | Upload size limit (5 MB) |

---

## 8. Running it

Mounted on the main app (the normal case):

```bash
uvicorn app:app --reload --port 8000
# form:   http://localhost:8000/gym/
# poster: http://localhost:8000/gym/poster
```

Standalone, with no part of the RTU benchmarking tool involved:

```bash
uvicorn gymform.standalone:app --host 0.0.0.0 --port 8000
# form is then at the site root: http://localhost:8000/
```

Tests:

```bash
pytest tests/test_gym_form.py
```

---

## 9. Editing the rules later

Every rule, fee slab and operating hour lives in `gymform/rules.py` as plain
data. Change the text or a fee there and the form, the acknowledgements, the
rules page, the printed poster and the notification emails all update together.
Rule acknowledgements are stored against stable keys, so rewording a rule never
invalidates registrations that were already submitted.
