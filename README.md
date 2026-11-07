# Pluck

A design inspiration library built with plain HTML/CSS/JavaScript and Python. Includes passwordless sign-in, X imports, boards, responsive masonry, and image previews. Local development uses SQLite and files; shared staging uses Supabase Postgres and private storage.

## Run

Python 3 and Node/npm are required for the scripts:

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
npm start
```

Open http://127.0.0.1:5173. `npm run check` checks syntax; `npm test` runs 23 tests covering accounts, private media access and caching, persistence, provider behavior, and image processing.

## Accounts and existing data

The login screen supports X and passwordless email. Pluck sends a single-use email link that expires after 15 minutes. After verification, it opens an existing account or creates one for a new address. Sessions last 90 days and renew when the app opens; expired sessions require sign-in again. Existing email accounts keep their library. The first account created claims the legacy library. New accounts start with All. Users create additional boards as needed.

For local testing, set `LOCAL_EMAIL_PREVIEW=1` in `.env` and restart. Submitting an email shows an **Open local sign-in link** action. Accounts persist in `data/moodboard.sqlite3`. This mode skips inbox ownership verification and is intended for loopback development only.

Set `SMTP_HOST`, `SMTP_PORT`, and `SMTP_FROM` in `.env` to enable email sign-in. The mail server must support implicit TLS. Set `SMTP_USERNAME` and `SMTP_PASSWORD` if it requires authentication.

To connect X or Google to an existing library, sign in first and use the account menu. Pluck links accounts after you prove control of both sign-ins.

For local testing, open http://127.0.0.1:5173 and register these exact callback URLs:

- Google web OAuth client: `http://127.0.0.1:5173/api/auth/google/callback`
- X OAuth 2.0 Web App (confidential client): `http://127.0.0.1:5173/api/auth/x/callback`

Copy `.env.example` to `.env`, fill in the matching client IDs and secrets, and restart with `npm start`. Exported settings take precedence over `.env`. Use the exact same host in the browser and callback settings. For Google projects in testing mode, add your Google account as a test user. For deployment, set `APP_ORIGIN` to the HTTPS origin and register callbacks there. X login uses a user-profile API request, subject to your X developer access and billing.

## Images and masonry

Uploads and X imports are decoded and orientation-corrected on the backend. Each record stores `width`, `height`, `url`, and `thumbnail_url` (plus the legacy `image` alias). Original images are preserved at full resolution in orientation-normalized PNGs; new WebP thumbnails are bounded by 640 × 1600 pixels without cropping. Existing thumbnails retain their saved size. Private media is browser-cached with session-cookie variation. Legacy records are backfilled automatically at startup. Back up the entire `data/` directory before moving machines or deploying.

The frontend reserves image space with stored CSS aspect ratios and positions cards in the shortest equal-width column. Columns: 1 below 701px, 3 through 1100px, 4 on desktop, and 5 above 1799px. Images below the first visible group lazy-load, with a grey ratio-matched skeleton. Only thumbnails are used in the grid; full-resolution images load when opening the lightbox. Tall images cap their preview box at approximately 2.5 times the width, remain uncropped, and offer a full-image view.

The preview opens with the cached thumbnail, then swaps to a full-resolution WebP. New imports generate this derivative when saved; existing saves generate it on first preview. Original PNGs remain available for clipboard copying. Derivatives use the same owner checks and private browser cache as other media.

## X imports

Without a token, public posts are looked up through FxTwitter (`https://api.fxtwitter.com/status/{id}`). Only the numeric post ID is sent to that provider. Attached photos and author profile pictures download from the allowlisted `pbs.twimg.com` host; creator and canonical source link are retained. Profile photos are cached locally, with initials as a fallback. Existing imports are deduplicated per account by canonical source.

Optionally set `X_BEARER_TOKEN` in `.env` for the official X API. Private, deleted, restricted, text-only, and video-only posts are unsupported. Availability depends on the chosen provider.

## Motion

Follows Emil Kowalski's `emil-design-eng` guidance from https://github.com/emilkowalski/skills. Short ease-out modal enter/exit, 140ms press feedback, restrained screen transitions, and image opacity reveals. The landing heading, copy, and button use a 180ms stagger. No gallery entrance stagger or image-load layout animation. Browser-native smooth scrolling respects reduced-motion preferences. Keyboard actions and reduced-motion preferences skip movement. Modals use native dialog focus trapping and Escape handling.

## Before public deployment

The Python HTTP server is intended for development and limited staging tests. Render terminates HTTPS; configure the shared staging credentials below. A public launch needs a production server, broader API rate limiting, backups, and retention/removal policies. Deleting a design currently removes its record but retains its image files on disk. Account avatars come from social sign-in; email-only accounts show an initial. Existing social accounts refresh their profile on the next sign-in.

## Project layout

- `index.html`, `styles.css`, `app.js`: frontend and native browser interactions.
- `server.py`: HTTP routes and application startup.
- `database.py`, `object_store.py`: local and hosted persistence.
- `auth.py`: accounts and sessions.
- `email_login.py`: email delivery and single-use sign-in links.
- `media.py`: image validation, dimensions, and thumbnails.
- `assets/`: only assets used by the app.
- `tests/`: isolated API, import, and image-processing checks.
- `data/`: private runtime data and restoration backups; excluded from Git.

Pillow handles images; psycopg connects hosted Postgres. These are the only runtime dependencies. No frontend framework, scroll library, or animation package is installed. Generated screenshots and the standalone component demo have been removed. Database initialization runs only when starting the server; importing modules in tests does not touch the real database.


## Shared staging

The `staging` branch includes a free Render Blueprint in `render.yaml`. The
public landing page continues to use the separate Vercel build.

1. Create a Supabase project. In Storage, create a **private** bucket named
   `pluck-media`. The backend rejects a public bucket.
2. Copy the **Transaction pooler** URI from Supabase's **Connect** dialog into
   `DATABASE_URL`, replacing the password placeholder. Use an URL-encoded
   password if it contains URI punctuation.
3. Set `SUPABASE_URL` and the legacy `SUPABASE_SERVICE_ROLE_KEY`. These keys stay
   on the Python server. Media is served through the existing owner-checked
   routes. Supabase tables live in the separate `pluck` schema.
4. Create a Resend sending key and verify your sending domain. Set
   `RESEND_API_KEY` and `EMAIL_FROM`, for example
   `Pluck <hello@yourdomain.com>`. The Resend test sender only delivers to the
   account owner's verified address, so it is insufficient for other testers.
5. In Render, create a **Blueprint** from this repository's `staging` branch.
   Supply the requested environment variables. Set `APP_ORIGIN` to the service's
   HTTPS URL without a trailing slash. If necessary, update it after Render
   assigns the service URL, then redeploy.
6. To enable X sign-in, add `X_OAUTH_CLIENT_ID` and
   `X_OAUTH_CLIENT_SECRET` to Render. Add
   `https://YOUR-SERVICE.onrender.com/api/auth/x/callback` to the X developer
   app's allowed callback URLs. Use the matching Google callback path if Google
   is enabled.
7. Test sign-in, X import, board creation, image copying and deletion. Redeploy
   and verify accounts and saved images still persist before inviting testers.

Render supplies `PORT`. The Blueprint binds to `0.0.0.0`, enables the full-app
landing buttons, and disables local mock sign-in. Render serves the application,
Supabase stores Postgres records and private media, and Resend sends email links
over HTTPS. No Render persistent disk is needed.

Existing local data is not automatically copied to Supabase. Staging starts
with separate accounts and boards. Keep the local `.env` out of Git and copy
only the relevant values into Render's environment settings.

Free Render services sleep after inactivity. Supabase and Resend free tiers
have usage limits. Resend requires a domain you control to send to other testers.

## Development storage

Without `DATABASE_URL` and Supabase variables, the app uses the existing local
SQLite database and files. `LOCAL_EMAIL_PREVIEW=1` works only on loopback.
Keep hosted keys out of a local mock environment if you want to test isolated
SQLite accounts.
