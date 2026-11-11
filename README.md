# Pluck

**Your UI inspiration, organized.**

Save designs from X, organize them into boards, and revisit the original posts whenever you need inspiration.

**[Open Pluck →](https://pluck-app-three.vercel.app/)**

Pluck is live in your browser. No installation, terminal commands, or npm setup is needed to use it.

## Getting started

1. Open [Pluck](https://pluck-app-three.vercel.app/).
2. Continue with X, or enter your email and open the sign-in link sent to your inbox.
3. Create a board, paste an X post link, and choose where to save its designs.

New accounts start with **All**, the parent library. Custom boards appear only when you create them. Returning users with a valid session go directly to their library; sessions last up to 90 days and renew when the app opens.

## Features

- Import images from public X posts, with the creator and source link retained.
- Create, rename, and delete boards. Board names allow **15 characters**.
- Browse a responsive masonry library with image aspect ratios preserved, skeleton placeholders, and lazy loading.
- Open larger image previews, copy images to the clipboard where supported, and view the original post.
- Use mobile bottom drawers and restrained transitions that respect reduced-motion preferences.

**All** cannot be renamed or deleted. Deleting a custom board also removes its saved designs from the entire library and any other boards containing them; Pluck asks for confirmation first.

Private, deleted, restricted, text-only, and video-only X posts are unsupported. Import availability depends on the source provider.

## Live environments

| Environment | URL | Branch |
| --- | --- | --- |
| Production app | [pluck-app-three.vercel.app](https://pluck-app-three.vercel.app/) | `main` |
| Staging | [pluck-staging.onrender.com](https://pluck-staging.onrender.com/) | `staging` |

Production and staging use separate Supabase projects. Accounts and images are not copied between them.

The production deployment contains only the app. A separate landing page can link to the app URL. The optional landing-page source and manual build remain separate from the app build.

## How it is hosted

- **Vercel:** static HTML, CSS, JavaScript, fonts, and icons.
- **Render:** Python API, authentication, and X imports.
- **Supabase:** PostgreSQL account and board records, plus private image storage.
- **Resend:** single-use email sign-in links, which expire after 15 minutes.

Vercel proxies API and private-media requests to Render under the app's domain. Session cookies are HttpOnly and Secure. Optimized thumbnails load in the library; larger previews load on demand. Supabase signed URLs allow authorized images to load directly from storage.

The backend currently uses Render's free plan, which can delay the first API request after inactivity.

<details>
<summary><strong>Developer setup and maintenance</strong></summary>

Local setup is only needed to work on the code—not to use the live app.

### Run locally

Use Python 3 and install the two runtime dependencies, Pillow and psycopg:

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
.venv/bin/python server.py
```

Open `http://127.0.0.1:5173`. Without hosted database and storage settings, development uses SQLite and files in the ignored `data/` directory.

For isolated local email testing, set `LOCAL_EMAIL_PREVIEW=1`. This exposes a local sign-in link and must remain disabled on hosted services. Keep production keys out of local mock environments.

### Checks

```sh
node --check app.js
node --check landing.js
.venv/bin/python -m unittest discover -s tests -v
```

The suite currently contains 33 tests covering accounts, session renewal, private media, persistence, imports, image processing, and deployment packaging. Node is needed only for the JavaScript syntax checks; npm scripts remain optional shortcuts.

### Deployments

- Pushing to `main` deploys the production Vercel app and production Render backend.
- Pushing to `staging` updates the separate Render staging service.
- Vercel's Production environment uses `PLUCK_API_ORIGIN=https://pluck-api-n4su.onrender.com`.
- The Vercel build is `python3 scripts/build_app.py .vercel/output`. It excludes backend source, environment files, runtime data, and landing-page assets.
- Preview deployments require an explicitly configured `PLUCK_API_ORIGIN`; they do not silently use production.
- Render configuration templates are in `render.yaml` and `render.production.yaml`.

Keep database, storage, Resend, and X OAuth secrets on the backend. Local `.env` and `.env.production` files are ignored by Git. `APP_ORIGIN` must match the public app origin, and the matching `/api/auth/x/callback` URL must be registered with X. Keep the staging callback registered too.

### Project layout

- `index.html`, `app.js`, `styles.css`, `fonts.css`: app interface.
- `server.py`: API routes and startup.
- `auth.py`, `oauth.py`, `email_login.py`: sessions and sign-in.
- `database.py`, `object_store.py`: local and hosted persistence.
- `media.py`: image validation, dimensions, and derivatives.
- `scripts/`: separate app and landing builds.
- `tests/`: isolated automated checks.

</details>
