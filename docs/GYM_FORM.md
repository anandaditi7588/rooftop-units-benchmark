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

## 0. What this costs

**Nothing.** Every part of the running system has a free option, and the
defaults use them:

| Piece | Free option | Cost |
|---|---|---|
| Hosting | Render free tier — 750 instance hours/month, no credit card | ₹0 |
| The form, QR code, poster, storage | Runs on that instance | ₹0 |
| Email to the office and the trainer | Gmail SMTP with an App Password (~500 mails/day) | ₹0 |
| WhatsApp — manual | One-tap `wa.me` link in the office email (default) | ₹0 |
| WhatsApp — automatic | CallMeBot, free for personal use (§4, option B) | ₹0 |

750 instance hours covers one service running every hour of a 31-day month
(744), so a single form service stays inside the free allowance.

The only things that ever cost money are optional upgrades you do not need:
keeping the service awake instead of sleeping when idle (~$7/month), and the
commercial WhatsApp providers (Twilio, Meta) if you outgrow the free relay.

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

### Read this first if you are on free hosting

**Render's free tier blocks outbound SMTP** — ports 25, 465 and 587 — since
26 September 2025. Gmail SMTP therefore cannot work from a free instance
regardless of how correct your App Password is; the send fails with
`[Errno 101] Network is unreachable`. Render's own advice is to upgrade to a
paid instance or use an email API.

So on free hosting, use **Brevo**, which sends over HTTPS (port 443, never
blocked). It is free for 300 emails a day, forever, with no card.

### Brevo setup (5 minutes)

1. Sign up at [brevo.com](https://www.brevo.com).
2. **Verify your sender address.** Left menu → *Senders, Domains & Dedicated
   IPs* → *Senders* → *Add a sender* → enter `dulange111@gmail.com`. Brevo
   emails you a confirmation link; click it.
3. **Create an API key.** Top-right menu → *SMTP & API* → *API Keys* →
   *Generate a new API key*. Copy it — it starts with `xkeysib-`.
4. Set on the server:

   ```bash
   GYM_BREVO_API_KEY=xkeysib-...................
   GYM_EMAIL_FROM=dulange111@gmail.com   # must match the verified sender
   ```

If the sender is not verified, Brevo rejects the send and the confirmation page
says exactly that rather than failing silently.

### SMTP (paid instances, or self-hosting)

Where outbound SMTP is allowed, plain SMTP still works and needs no third-party
account. With Gmail:

1. Turn on 2-Step Verification on the account that will *send* the mail.
2. Create an **App Password**: Google Account → Security → 2-Step Verification →
   App passwords. You get a 16-character password.
3. Set:

   ```bash
   GYM_SMTP_USER=the.sending.account@gmail.com
   GYM_SMTP_PASSWORD=abcdefghijklmnop     # the 16-character App Password
   ```

> A normal Gmail password will **not** work — Google blocks it for SMTP.

`GYM_BREVO_API_KEY` takes priority when both are set, because it works in
strictly more places. `/health` reports which provider is live as
`email_provider`.

### What gets sent

The office receives a formatted summary with the client list, the calculated
fee, any rule warnings, and the uploaded ID proof attached. The trainer
receives their own confirmation with their reference number and what they
agreed to (switch off with `GYM_SEND_TRAINER_COPY=0`).

Check it works without waiting for a real trainer:

```bash
curl -u office:yourpassword -X POST https://<your-host>/admin/test-notification
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

### Option B — CallMeBot (free, automatic)

A free relay that will message one pre-authorised number. No account, no card.

1. Save **+34 644 51 95 23** in the contacts of the phone that will *receive*
   the alerts (+91 7588610829).
2. From that phone, WhatsApp it: `I allow callmebot to send me messages`
3. It replies with an API key. Set it on the server:

   ```bash
   GYM_CALLMEBOT_APIKEY=123456
   ```

Two caveats worth weighing before you switch it on. Its terms cover **personal
use**, and a residents' association is a grey area — read them and decide. And
messages pass through a third party's servers, which is why the WhatsApp text
deliberately carries **no ID number and no home address**: it names the trainer,
their mobile, the client list and the fee, and leaves the identity documents to
the email and the office review page.

If neither sits well, option A costs nothing either and keeps every trainer's
detail between your server and your own inbox.

### Option C — Twilio (commercial, free trial credit)

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

### Option D — WhatsApp Cloud API (direct from Meta)

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
| `GYM_BREVO_API_KEY` | — | Brevo API key — sends over HTTPS, works on free hosting |
| `GYM_EMAIL_FROM` | office address | The "from" address; must be verified in Brevo |
| `GYM_SMTP_HOST` | `smtp.gmail.com` | SMTP server (ignored when Brevo is set) |
| `GYM_SMTP_PORT` | `587` | SMTP port |
| `GYM_SMTP_USER` | — | SMTP login (email sending is off until set) |
| `GYM_SMTP_PASSWORD` | — | SMTP password / Gmail App Password |
| `GYM_SMTP_FROM` | `GYM_SMTP_USER` | From address, if different |
| `GYM_SMTP_USE_SSL` | `0` | `1` for implicit SSL (port 465) instead of STARTTLS |
| `GYM_CALLMEBOT_APIKEY` | — | CallMeBot key (enables free automatic WhatsApp) |
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

---

## 10. Deploying to Render (free tier)

Deploy **the form only**, not the whole repository. The form shares this repo
with the RTU benchmarking tool but none of its code, so it needs six small
packages rather than that tool's pandas/PyMuPDF/scikit-learn stack — which has
previously run the free tier out of memory. `render.yaml` and
`gymform/requirements.txt` are set up for exactly this.

### Steps

1. Go to [dashboard.render.com](https://dashboard.render.com) and sign up (the
   free tier needs no card).
2. **New → Blueprint**, connect your GitHub account, and pick the
   `rooftop-units-benchmark` repository. Render reads `render.yaml` and offers
   a service called **silicon-bay-gym-form**. Approve it.
3. Wait for the first build (2-3 minutes). You now have a live address like
   `https://silicon-bay-gym-form.onrender.com`.
4. Open **Environment** on the service and set the four secrets the blueprint
   left blank:

   | Key | Value |
   |---|---|
   | `GYM_ADMIN_USERNAME` | any username you choose, e.g. `office` |
   | `GYM_ADMIN_PASSWORD` | a password you choose |
   | `GYM_SMTP_USER` | the Gmail address that sends the mail |
   | `GYM_SMTP_PASSWORD` | that account's 16-character **App Password** (§3) |

   Save — Render redeploys automatically.
5. Confirm it works: open `https://<your-address>/admin/test-notification`'s
   sibling check first — `https://<your-address>/health` should return
   `"email_configured": true`. Then send a test registration to yourself:

   ```bash
   curl -u office:yourpassword -X POST https://<your-address>/admin/test-notification
   ```

6. Print the QR notice from `https://<your-address>/poster`. The QR encodes the
   right URL automatically — Render sets `RENDER_EXTERNAL_URL`, and the form
   reads it, so there is nothing to configure.

Note that on the standalone deployment the form sits at the **site root**, so
the paths have no `/gym` prefix: the form is `/`, the poster is `/poster`, the
office pages are `/admin`.

### Two free-tier facts worth knowing

- **It sleeps after ~15 minutes idle.** The first scan after a quiet spell
  takes 30-60 seconds to wake the service. Trainers see a slow load, not an
  error. A paid instance ($7/month) stays awake.
- **The disk is wiped on every redeploy**, so `output/gym_form/` is not durable
  storage. The notification email — which carries the full summary and the ID
  proof attachment — is the durable copy. Download the CSV from
  `/admin/submissions.csv` periodically if you want a local archive.

---

## 11. Troubleshooting

### "502 Bad Gateway" right after submitting

The submission itself almost certainly succeeded — check `/admin`, and note the
reference in the address bar (`/submitted/SB-PT-...`). A 502 means the host
could not reach the service *at that moment*, not that the form rejected
anything.

The cause fixed in this repository was the notification send running on the
web server's event loop: a slow mail server froze every other request,
including Render's health check, and Render restarted the instance mid-flow.
Sending now happens off the loop. If you see a 502 again, check the service's
**Logs** tab in Render for the real reason, and confirm the instance is not
simply cold-starting from idle sleep (the first request after ~15 minutes of
inactivity can take up to a minute).

### The confirmation page says "Not sent" for email

Open `/health`. If `email_configured` is `false`, `GYM_SMTP_USER` or
`GYM_SMTP_PASSWORD` is missing on the host. If it is `true` but mail still
fails, the confirmation page prints the SMTP error — an authentication failure
almost always means a normal Gmail password was used instead of a 16-character
App Password.

### The QR code points at the wrong address

Open `/health` and read `form_url` — that is exactly what the QR encodes. On
Render it comes from `RENDER_EXTERNAL_URL` automatically. Behind a custom
domain, set `GYM_PUBLIC_URL` to the address trainers should reach.
