# page-monitor

Watches one or more web pages and pushes a phone notification (via [ntfy](https://ntfy.sh))
whenever a page's content changes. Runs for free on GitHub Actions on a schedule -
your computer does **not** need to be on.

Currently watching: the CNGL "admitere clasa a V-a 2026-2027" event page.

## How it works

1. A scheduled GitHub Actions job (every ~30 min) runs `monitor.py`.
2. For each watch in `config.json`, it fetches the page, extracts the visible text
   of the `main` content region (ignoring site header/footer/scripts), and compares
   it to the last saved snapshot in `state/<slug>.txt`.
3. If the text changed, it POSTs an alert to your ntfy topic (with a diff of what
   changed and a click-through link), then saves the new snapshot.
4. The updated snapshot is committed back to the repo - so `git log -p state/`
   is a full, human-readable history of every change to the page.

No database, no server, no secrets beyond a single ntfy topic name.

## Setup

### 1. Pick an ntfy topic and subscribe on your phone
- Install the **ntfy** app (Android / iOS) or open <https://ntfy.sh/app>.
- Choose a hard-to-guess topic name (it acts like a password - anyone who knows it
  can read your alerts). Example: `cngl-admitere-7h3k9q`.
- In the app: **Subscribe to topic** -> enter that name.

### 2. Add the topic as a repo secret
In the GitHub repo: **Settings -> Secrets and variables -> Actions -> New repository secret**
- Name: `NTFY_TOPIC`
- Value: your topic (e.g. `cngl-admitere-7h3k9q`)

Or from the CLI:
```bash
gh secret set NTFY_TOPIC --body "cngl-admitere-7h3k9q"
```

### 3. Trigger the first run
- **Actions** tab -> **page-monitor** -> **Run workflow**.
- The first run saves a baseline and sends a "Monitoring started" notification -
  if that lands on your phone, the whole pipeline works. After that you only get
  pinged when the page actually changes.

## Customizing

- **Check more / less often:** edit the `cron` in `.github/workflows/monitor.yml`.
  `*/15 * * * *` = every 15 min. (GitHub may delay scheduled runs a few minutes
  under load - the interval is approximate.)
- **Watch another page:** add an object to `watches` in `config.json`:
  ```json
  { "name": "My other page", "url": "https://example.com/x", "selector": "main" }
  ```
- **Too many false alerts (ads/counters):** narrow `selector` to a more specific
  CSS selector for just the part you care about. Omit `selector` to hash the whole
  `<body>`.

## Test locally
```bash
pip install -r requirements.txt
DRY_RUN=1 python monitor.py    # prints notifications instead of sending them
```

## Caveats

- **JavaScript-rendered pages:** this fetches raw HTML. If a page loads its content
  via JS (this one does not), the text won't be visible and you'd need a headless
  browser. Not needed here.
- **GitHub disables scheduled workflows after 60 days of repo inactivity.** The
  snapshot commits count as activity, so an actively-changing page keeps it alive;
  otherwise push any commit to re-enable.
- **ntfy topic privacy:** topics on the public ntfy.sh server are not encrypted at
  rest and are readable by anyone who knows the name. Fine for public info like this;
  use a random name and/or self-host ntfy for anything sensitive.
