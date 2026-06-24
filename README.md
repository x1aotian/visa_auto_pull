# US Visa Slot Monitor

This project monitors the public appointment-slot data shown by [qmq.app](https://qmq.app/). It filters by city, visa type, and date range, then sends alerts when matching slots appear.

There are two supported deployment paths:

- **Cloudflare Workers Cron Triggers**: best for high frequency. Runs every minute and uses Cloudflare KV to avoid duplicate alerts.
- **GitHub Actions**: easiest if you want to keep the Python script and Gmail SMTP. Runs every 5 minutes because GitHub scheduled workflows are not reliable at one-minute cadence.

## What You Need

Choose your target filters:

- `TARGET_CITIES`: comma-separated city keys or names, for example `cnGUA,Guangzhou,广州`
- `TARGET_VISA_TYPES`: comma-separated visa text fragments, for example `F-1,B1/B2`
- `DATE_FROM`: earliest appointment date, `YYYY-MM-DD`
- `DATE_TO`: latest appointment date, `YYYY-MM-DD`

Common China city keys:

- `cnBEI`: Beijing / 北京
- `cnSHA`: Shanghai / 上海
- `cnGUA`: Guangzhou / 广州
- `cnSHE`: Shenyang / 沈阳
- `cnWUH`: Wuhan / 武汉
- `cnCHE`: Chengdu / 成都

Other useful keys in the script include `hkHON`, `krSEO`, `jpTKY`, and `sgSGP`.

## WeChat Notification

This project uses **ServerChan** for personal WeChat push notifications.

1. Create or log into a ServerChan account.
2. Bind your WeChat account.
3. Copy your `SENDKEY`.
4. Set it as `SERVERCHAN_SENDKEY`.

Only ServerChan is supported for WeChat notifications.

## Option A: Cloudflare Workers, Every Minute

Use this if you want high frequency while staying free or very cheap. The Worker runs every minute with:

```toml
[triggers]
crons = ["* * * * *"]
```

Cloudflare Workers cannot send Gmail SMTP directly because Workers do not open SMTP socket connections. For Cloudflare, the supported notifications are:

- WeChat through `SERVERCHAN_SENDKEY`
- Optional email to your Gmail inbox through Resend's HTTP API

### 1. Install Cloudflare Wrangler

```bash
npm install
```

Then log in:

```bash
npx wrangler login
```

### 2. Create a KV Namespace

The Worker stores alert fingerprints in KV so it does not send the same slot repeatedly.

```bash
npx wrangler kv namespace create VISA_MONITOR_STATE
```

Wrangler prints an `id`. Copy that value into `wrangler.toml`:

```toml
[[kv_namespaces]]
binding = "VISA_MONITOR_STATE"
id = "your-kv-namespace-id"
```

### 3. Set Your Filters

Edit the `[vars]` block in `wrangler.toml`:

```toml
[vars]
TARGET_CITIES = "cnGUA"
TARGET_VISA_TYPES = "F-1"
DATE_FROM = "2026-07-01"
DATE_TO = "2026-08-31"
EMAIL_FROM = ""
EMAIL_TO = ""
```

For multiple cities or visa terms, use commas:

```toml
TARGET_CITIES = "cnGUA,cnSHA"
TARGET_VISA_TYPES = "F-1,B1/B2"
```

### 4. Add Secrets

For WeChat:

```bash
npx wrangler secret put SERVERCHAN_SENDKEY
```

For optional email alerts through Resend:

```bash
npx wrangler secret put RESEND_API_KEY
```

Then set these non-secret email values in `wrangler.toml`:

```toml
EMAIL_FROM = "Visa Monitor <alerts@your-domain.com>"
EMAIL_TO = "yourname@gmail.com"
```

Resend requires a verified sending domain. If you only want WeChat, leave `EMAIL_FROM`, `EMAIL_TO`, and `RESEND_API_KEY` empty.

### 5. Test Locally

```bash
npx wrangler dev
```

Open the local URL shown by Wrangler and visit:

```text
/run?dry_run=1
```

Dry runs fetch and filter slots but do not send notifications or write dedupe state.

### 6. Deploy

```bash
npx wrangler deploy
```

After deployment, Cloudflare runs the cron every minute. You can inspect logs with:

```bash
npx wrangler tail
```

You can also manually trigger a deployed run by visiting:

```text
https://your-worker-name.your-subdomain.workers.dev/run?dry_run=1
```

Remove `?dry_run=1` only when you want it to send notifications and update dedupe state.

## Option B: GitHub Actions, Every 5 Minutes

Use this if you prefer GitHub and Gmail SMTP. The workflow is in `.github/workflows/visa-monitor.yml`.

GitHub Actions scheduled workflows use:

```yaml
- cron: "*/5 * * * *"
```

That is the smallest practical GitHub interval. For true one-minute checks, use Cloudflare Workers.

### 1. Create a Gmail App Password

1. Turn on 2-Step Verification for the Gmail account.
2. Go to Google Account security settings.
3. Create an app password for mail.
4. Save the generated password as `GMAIL_APP_PASSWORD`.

Do not use your normal Gmail password.

### 2. Add GitHub Repository Secrets

In your GitHub repo:

1. Go to **Settings**.
2. Go to **Secrets and variables**.
3. Open **Actions**.
4. Add these repository secrets:

```text
TARGET_CITIES
TARGET_VISA_TYPES
DATE_FROM
DATE_TO
GMAIL_USERNAME
GMAIL_APP_PASSWORD
EMAIL_TO
SERVERCHAN_SENDKEY
```

`SERVERCHAN_SENDKEY` is optional if you only want email. The Gmail variables are optional if you only want ServerChan.

Example values:

```text
TARGET_CITIES=cnGUA
TARGET_VISA_TYPES=F-1
DATE_FROM=2026-07-01
DATE_TO=2026-08-31
GMAIL_USERNAME=yourname@gmail.com
EMAIL_TO=yourname@gmail.com
```

### 3. Run Manually Once

In GitHub:

1. Go to the **Actions** tab.
2. Choose **Visa Slot Monitor**.
3. Click **Run workflow**.

If matching slots are found, the workflow sends alerts and stores a small `.cache/seen.json` file through GitHub Actions cache so future runs do not repeat the same alert.

## Local Test

Run a dry test from this folder:

```bash
TARGET_CITIES=cnGUA \
TARGET_VISA_TYPES=F-1 \
DATE_FROM=2026-07-01 \
DATE_TO=2026-08-31 \
python3 -B -m visa_monitor.monitor --dry-run
```

Dry runs print matches only. They do not send email, send WeChat notifications, or write dedupe state.

## My Current Monitor Config

Current target:

```text
TARGET_CITIES=cnSHE
TARGET_VISA_TYPES=H-1B
DATE_FROM=2026-06-24
DATE_TO=2026-07-29
SERVERCHAN_SENDKEY=<your ServerChan SENDKEY>
```

`DATE_FROM=2026-06-24` means "now" as of June 24, 2026.

Do not put `SERVERCHAN_SENDKEY` directly in this README. Store it as a secret instead:

For GitHub Actions:

```text
SERVERCHAN_SENDKEY=<your ServerChan SENDKEY>
```

For Cloudflare Workers:

```bash
npx wrangler secret put SERVERCHAN_SENDKEY
```

Local dry run with this config:

```bash
TARGET_CITIES=cnSHE \
TARGET_VISA_TYPES=H-1B \
DATE_FROM=2026-06-24 \
DATE_TO=2026-07-29 \
python3 -B -m visa_monitor.monitor --dry-run
```

## How Dedupe Works

Each matched slot is converted into a fingerprint using:

- city
- visa class
- slot date
- listed times
- source updated time

Cloudflare stores fingerprints in KV. GitHub Actions stores them in `.cache/seen.json` and restores that folder with the Actions cache.

If qmq.app changes the slot date, time, visa class, or updated timestamp, the fingerprint changes and a new alert can be sent.

## Choosing a Host

Use **Cloudflare Workers** if:

- You want every-minute checks.
- You mainly need WeChat alerts.
- You are okay using Resend or another HTTP email provider instead of Gmail SMTP.

Use **GitHub Actions** if:

- Five-minute checks are enough.
- You want the simplest setup.
- You want Gmail SMTP with an app password.
