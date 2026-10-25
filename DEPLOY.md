# Deploying DRAIN OUT

The site runs in two parts:

| Part | Where | What it serves |
|---|---|---|
| **Pages** | Vercel (`https://<project>.vercel.app`) | The Dashboard, Resources and Technology pages, scripts, styles, ward previews |
| **Engine** | Hugging Face Spaces, Docker (`https://<user>-<space>.hf.space`) | The flood model, routing and every `/data-collection/...` API call |

Vercel forwards `/data-collection/*` and `/resources/ward-map/*` to the engine
(`web/vercel.json`), so the pages call the API on their own address: no CORS, and no
change to the pages' code. The engine cannot run on Vercel itself: it needs about
360 MB of libraries and data (Vercel's limit is 250 MB), minutes to build its model,
a disk cache and a background task.

## 1. Prepare the engine (on this computer)

```sh
python scripts/prepare_space.py --out ../drainout-space --with-pdfs --with-history
```

Drop `--with-pdfs` to leave out the 215 MB of ward PDFs (the ward cards then show the
previews only), and `--with-history` to leave out the historical flood study (the
forecast then shows no flood history).

## 2. Create the Space and push the engine

1. Sign in at https://huggingface.co and create a **new Space**: SDK **Docker**,
   hardware **CPU basic (free)**, visibility **Public**.
2. In the Space's **Settings → Variables and secrets**, add the secret
   `OPENWEATHER_API_KEY` (the weather map tiles need it).
3. Push the folder from step 1 (Git LFS carries the large files):

```sh
cd ../drainout-space
git init
git lfs install
git remote add origin https://huggingface.co/spaces/<user>/<space>
git add .
git commit -m "DRAIN OUT flood engine"
git push -u origin main        # password: a Hugging Face access token with write access
```

The first build installs the libraries and builds the city model inside the image
(about 10-20 minutes). When the Space shows **Running**, open
`https://<user>-<space>.hf.space/data-collection/engine`: it should return JSON.

## 3. Build the pages for Vercel

```sh
python scripts/build_frontend.py --engine https://<user>-<space>.hf.space
```

This writes `web/`: three HTML pages, the static files and `vercel.json`.

## 4. Deploy the pages to Vercel

```sh
npx vercel login
npx vercel deploy web --prod
```

Accept the defaults (no build command, output is the folder itself). Vercel prints the
address, e.g. `https://drainout.vercel.app`.

## Updating

- **Pages changed:** run step 3 again, then `npx vercel deploy web --prod`.
- **Model or API changed:** run step 1 again into the same folder, then commit and push
  it (step 2.3); the Space rebuilds.

## Limits to know

- A free Space **sleeps after 48 hours without visitors**. The first visit after that
  wakes it, which takes a few minutes; the Dashboard shows its loading card meanwhile.
- The live
  forecast and the preset storms are computed ahead and answer at once; a new what-if
  storm takes about 15-25 s the first time.
- Phones share GPS only on HTTPS: both addresses are HTTPS, so "Use my location" works.

## Running the engine from this computer (current setup)

Hugging Face now charges for Docker Spaces, and Render's free plan (512 MB) is too small
for the engine (about 0.7 GB idle). Until the engine has a
paid host, it runs on this computer behind a free Cloudflare quick tunnel:

```sh
C:\Users\quant\anaconda3\envs\End-to-End\python.exe scripts/go_live.py
```

It starts the engine, opens the tunnel, rebuilds `web/` for the tunnel's address and
deploys it to Vercel. The site works while the command runs and the computer is awake.
`cloudflared.exe` is expected in `%USERPROFILE%\bin`.
