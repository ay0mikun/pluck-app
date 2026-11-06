# Pluck

A working local moodboard app implemented from six Figma nodes, using plain HTML/CSS/JavaScript, Python, SQLite, and Pillow. Includes landing, auth, empty library, populated masonry, X import dialog,.

## Run

Python 3 and Node/npm are required for the scripts:

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
npm start
```

Open http://127.0.0.1:5173. `npm run check` checks syntax; `npm test` runs 12 tests covering accounts, private media access, persistence, provider behavior, and image processing.

## Accounts and existing data

A single Get started form signs in existing email/password accounts or creates an account when the email is new. Existing accounts must supply the correct password. Passwords use salted scrypt; server sessions use hashed random tokens and HttpOnly, SameSite cookies. Boards, designs, and media are scoped to the signed-in user. The **first account created claims the existing legacy library**, so no previously saved designs are lost. Later accounts start with an empty library and six default moodboards.

Google and X sign-in use separate provider identities. To keep an existing library, sign in with your password and choose **Connect Google** or **Connect X** from the account menu. Accounts are linked only after you prove control of both sign-ins.

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

The server binds to localhost and is a development server. Public deployment still needs HTTPS and a production server, auth/API rate limiting, secure-cookie configuration, email verification/reset delivery, production OAuth credentials, managed storage/database and backups, and retention/removal policies. Deleting a design currently removes its record but retains its image files on disk. The reference avatar is a local placeholder, not a fetched account identity.

## Project layout

- `index.html`, `styles.css`, `app.js`: frontend and native browser interactions.
- `server.py`: HTTP routes and SQLite storage.
- `auth.py`: password hashing and sessions.
- `media.py`: image validation, dimensions, and thumbnails.
- `assets/`: only assets used by the app.
- `tests/`: isolated API, import, and image-processing checks.
- `data/`: private runtime data and restoration backups; excluded from Git.

Pillow is the only runtime dependency. No frontend framework, scroll library, or animation package is installed. Generated screenshots and the standalone component demo have been removed. Database initialization runs only when starting the server; importing modules in tests does not touch the real database.
