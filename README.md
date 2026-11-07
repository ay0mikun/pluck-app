# Pluck

A working local moodboard app implemented from six Figma nodes, using plain HTML/CSS/JavaScript, Python, SQLite, and Pillow. Includes landing, auth, empty library, populated masonry, X import dialog,.

## Run

Python 3 and Node/npm are required for the scripts:

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
npm start
```

Open http://127.0.0.1:5173. `npm run check` checks syntax; `npm test` runs 13 tests covering accounts, private media access, persistence, provider behavior, and image processing.

## Accounts and existing data

The login screen supports X and passwordless email. Pluck sends a single-use email link that expires after 15 minutes. After verification, it opens an existing account or creates one for a new address. Existing email accounts keep their library. The first account created claims the legacy library. New accounts start with All. Users create additional moodboards as needed.

For local testing, set `LOCAL_EMAIL_PREVIEW=1` in `.env` and restart. Submitting an email shows an **Open local sign-in link** action. Accounts persist in `data/moodboard.sqlite3`. This mode skips inbox ownership verification and is intended for loopback development only.

Set `SMTP_HOST`, `SMTP_PORT`, and `SMTP_FROM` in `.env` to enable email sign-in. The mail server must support implicit TLS. Set `SMTP_USERNAME` and `SMTP_PASSWORD` if it requires authentication.

To connect X or Google to an existing library, sign in first and use the account menu. Pluck links accounts after you prove control of both sign-ins.

For local testing, open http://127.0.0.1:5173 and register these exact callback URLs:

- Google web OAuth client: `http://127.0.0.1:5173/api/auth/google/callback`
- X OAuth 2.0 Web App (confidential client): `http://127.0.0.1:5173/api/auth/x/callback`

Copy `.env.example` to `.env`, fill in the matching client IDs and secrets, and restart with `npm start`. Exported settings take precedence over `.env`. Use the exact same host in the browser and callback settings. For Google projects in testing mode, add your Google account as a test user. For deployment, set `APP_ORIGIN` to the HTTPS origin and register callbacks there. X login uses a user-profile API request, subject to your X developer access and billing.

## Images and masonry

Uploads and X imports are decoded and orientation-corrected on the backend. Each record stores `width`, `height`, `url`, and `thumbnail_url` (plus the legacy `image` alias). Original images are preserved at full resolution in orientation-normalized PNGs; WebP thumbnails are bounded by 720 × 1800 pixels without cropping. Legacy records are backfilled automatically at startup. Back up the entire `data/` directory before moving machines or deploying.

The frontend reserves image space with stored CSS aspect ratios and positions cards in the shortest equal-width column. Columns: 2 below 701px, 3 through 1100px, 4 on desktop, and 5 above 1799px. Images below the first visible group lazy-load, with a grey ratio-matched skeleton. Only thumbnails are used in the grid; full-resolution images load when opening the lightbox. Tall images cap their preview box at approximately 2.5 times the width, remain uncropped, and offer a full-image view.

## X imports

Without a token, public posts are looked up through FxTwitter (`https://api.fxtwitter.com/status/{id}`). Only the numeric post ID is sent to that provider. Attached photos and author profile pictures download from the allowlisted `pbs.twimg.com` host; creator and canonical source link are retained. Profile photos are cached locally, with initials as a fallback. Existing imports are deduplicated per account by canonical source.

Optionally set `X_BEARER_TOKEN` in `.env` for the official X API. Private, deleted, restricted, text-only, and video-only posts are unsupported. Availability depends on the chosen provider.

## Motion

Follows Emil Kowalski's `emil-design-eng` guidance from https://github.com/emilkowalski/skills. Short ease-out modal enter/exit, 140ms press feedback, restrained screen transitions, and image opacity reveals. The landing heading, copy, and button use a 60ms stagger. No gallery entrance stagger or image-load layout animation. Browser-native smooth scrolling respects reduced-motion preferences. Keyboard actions and reduced-motion preferences skip movement. Modals use native dialog focus trapping and Escape handling.

## Before public deployment

The server binds to localhost and is a development server. Public deployment still needs HTTPS and a production server, auth/API rate limiting, secure-cookie configuration, production email delivery, production OAuth credentials, managed storage/database and backups, and retention/removal policies. Deleting a design currently removes its record but retains its image files on disk. The reference avatar is a local placeholder, not a fetched account identity.

## Project layout

- `index.html`, `styles.css`, `app.js`: frontend and native browser interactions.
- `server.py`: HTTP routes and SQLite storage.
- `auth.py`: accounts and sessions.
- `email_login.py`: email delivery and single-use sign-in links.
- `media.py`: image validation, dimensions, and thumbnails.
- `assets/`: only assets used by the app.
- `tests/`: isolated API, import, and image-processing checks.
- `data/`: private runtime data and restoration backups; excluded from Git.

Pillow is the only runtime dependency. No frontend framework, scroll library, or animation package is installed. Generated screenshots and the standalone component demo have been removed. Database initialization runs only when starting the server; importing modules in tests does not touch the real database.


## Shared staging

The existing Vercel configuration builds the public landing page only. Run the
full app as a separate Python web service, with a separate database and uploads.

For Render, connect this repository and use these settings:

- Build command: `pip install -r requirements.txt`
- Start command: `python server.py`
- Set `HOST=0.0.0.0` and `PLUCK_STAGING=1`. Render supplies `PORT`.
- Attach a persistent disk at `/var/data` and set
  `MOODBOARD_DATA_DIR=/var/data/pluck`.
- Set `APP_ORIGIN` to the service's HTTPS URL, without a trailing slash.
- Set `LOCAL_EMAIL_PREVIEW=0` and `LOCAL_FILLED_LIBRARY=0`.
- Configure SMTP using the variables in `.env.example` for real email sign-in.
- Add the staging origin's `/api/auth/x/callback` and
  `/api/auth/google/callback` URLs in the corresponding provider consoles,
  then set their client IDs and secrets in Render's environment settings.

After deployment, test sign-in, saving an X post, creating a board, copying an
image, and deleting it. Restart the service and verify that accounts and saved
images persist before inviting testers.

Render requires a paid service for a persistent disk. Keep staging credentials
and data separate from the public launch environment.
