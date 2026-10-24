# Running Mapnificently on Google Cloud Run

[![Run on Google Cloud](https://deploy.cloud.run/button.svg)](https://deploy.cloud.run/?git_repo=https://github.com/Patechoc/mapnificently.git)

The button opens Google Cloud Shell, builds this repository's [`Dockerfile`](../Dockerfile) and
deploys it to Cloud Run **in your own Google Cloud project, billed to you** (a billing account
is needed; a demo that sleeps when idle costs little). The image contains the prepared data of
every configured region, so the app is ready as soon as it starts.

[`app.json`](../app.json) sets what the deployment uses: 1 GiB of memory, at most 2 instances
(a cap on costs), public access, and it asks for the name sent to Entur with address searches.

Good to know:

- **Data is as fresh as the image.** It is downloaded while building and valid until the
  feeds' end dates (`make data-status` shows them). Deploy again to refresh it.
- **Refreshing over HTTP is off** in the image (`MAPNIFICENTLY_ALLOW_REFRESH=false`), so
  nobody can make a public server re-download everything.
- **Building needs about 1.5 GB of memory** (preparing Bergen is the peak). If the build in
  Cloud Shell runs out, deploy without the button (below), which builds on Google's servers.
- **Map tiles** come from OpenStreetMap's public server, whose policy does not allow heavy
  traffic: switch the tile provider before sharing a deployment widely.
- **Cold starts:** an idle instance is stopped; the first request after a pause loads a
  city's data in about a second.

## Where to run the `gcloud` commands

Every command on this page uses `gcloud`, Google Cloud's CLI. Run them in either place:

- **Cloud Shell, in the browser** (nothing to install): open
  [console.cloud.google.com](https://console.cloud.google.com/), pick your project and click
  the Cloud Shell icon (`>_`, top right). `gcloud` is installed and already signed in to your
  Google account. The easiest choice for an occasional deployment.
- **Your own terminal**, after installing the CLI once. On Debian or Ubuntu, including WSL on
  Windows (other systems: [Google's install guide](https://cloud.google.com/sdk/docs/install)):

  ```bash
  sudo apt-get install -y apt-transport-https ca-certificates gnupg curl
  curl https://packages.cloud.google.com/apt/doc/apt-key.gpg \
    | sudo gpg --dearmor -o /usr/share/keyrings/cloud.google.gpg
  echo "deb [signed-by=/usr/share/keyrings/cloud.google.gpg] https://packages.cloud.google.com/apt cloud-sdk main" \
    | sudo tee /etc/apt/sources.list.d/google-cloud-sdk.list
  sudo apt-get update && sudo apt-get install -y google-cloud-cli
  gcloud init     # prints a sign-in link, then asks for the project
  ```

  Sign in with the Google account that owns the project.

## Deploy without the button

This builds on Google's build servers (Cloud Build), so it also avoids Cloud Shell's memory
limit:

```bash
git clone https://github.com/Patechoc/mapnificently.git && cd mapnificently
gcloud run deploy mapnificently --source . --region europe-north1 \
  --memory 1Gi --max-instances 2 --allow-unauthenticated
```

`europe-north1` (Finland) is the region closest to Norway; gcloud prints the app's address at
the end.

## If the button fails

**`Error 429: Quota exceeded … 'List available/disabled services requests per minute'`**
while "enabling required APIs": a per-minute rate limit on Google's side, not a problem with
the app or your project. Wait a minute and try again. If it keeps happening, check billing and
enable the APIs yourself (replace `PROJECT_ID` with your project's id):

```bash
gcloud config set project PROJECT_ID
gcloud billing projects describe PROJECT_ID   # must show: billingEnabled: true
gcloud services enable run.googleapis.com artifactregistry.googleapis.com cloudbuild.googleapis.com
```

Then run the button again, or paste in Cloud Shell what the button runs. Do it from an empty
folder: a clone left by an earlier attempt makes it fail with `fatal: destination path
'mapnificently' already exists`.

```bash
mkdir -p ~/deploy-$(date +%s) && cd "$_"
cloudshell_open --repo_url "https://github.com/Patechoc/mapnificently.git" --page "shell" --force_new_clone
```

## Update a running deployment

A deployment never updates itself: it keeps the image it was built with. Deploy again to:

- **refresh the timetables**, before the feeds' end dates;
- **pick up fixes** in the code, its dependencies or the base image. In October 2026, for
  example, the CA certificates had to be updated after a certificate authority (eMudhra) was
  compromised, and only a rebuilt image has them.

Find the service's name and region (the button names it `mapnificently`):

```bash
gcloud config set project PROJECT_ID
gcloud run services list
```

Then deploy from an up-to-date clean checkout of `main` (`--source .` uploads the working
tree, uncommitted changes included):

```bash
git clone https://github.com/Patechoc/mapnificently.git && cd mapnificently   # or: git pull
gcloud run deploy mapnificently --source . --region europe-north1 \
  --memory 1Gi --max-instances 2 --allow-unauthenticated
```

This builds a new image and deploys it as a new revision of the same service, so its address
does not change. Settings the command does not set, such as the Entur client name, carry over
from the previous revision. Pressing the button again also works, but builds in Cloud Shell.

Check that the new revision is serving (newest first, with today's date):

```bash
gcloud run revisions list --service mapnificently --region europe-north1 --limit 3
```

## After deploying: how long it runs, what it costs, removing it

**It stays deployed until you delete it**; a Cloud Run service does not expire. When nobody
uses it, it scales to zero and uses no compute; the next visitor wakes it in about a second.
The timetables in it do expire, at the feeds' end dates (`make data-status` shows them): after
that it still runs but finds no trips until you [deploy again](#update-a-running-deployment).

**What it costs:**

- **Idle:** nothing for compute.
- **Light use:** within Cloud Run's free monthly allowances (2 million requests, 180,000
  vCPU-seconds, 360,000 GiB-seconds per billing account).
- **Storage:** a few cents a month at most. The built image (about 200 MB) is kept in
  Artifact Registry, where 0.5 GiB is free; each deployment adds one. A deployment with
  `--source` also keeps a small copy of the source in a Cloud Storage bucket.
- **The real risk is traffic**, since the address is public. `app.json` and the commands
  above cap it at 2 instances; a **budget alert** (Billing → Budgets & alerts, e.g. $5) tells
  you if usage ever grows.

**Your options** (replace `europe-north1` if you deployed elsewhere):

| Option | When | What to do |
|---|---|---|
| Keep it public | an occasional demo | nothing; set a budget alert |
| Keep it, but private | only people with access to your project should open it | remove public access (below) |
| Remove it | no longer needed: no costs at all | delete the service, its images and the source copies (below) |

Make it private (only signed-in users with access to the project can open it); add the
binding back with `add-iam-policy-binding` and the same flags to make it public again:

```bash
gcloud run services remove-iam-policy-binding mapnificently --region europe-north1 \
  --member=allUsers --role=roles/run.invoker
```

Remove it completely:

```bash
# 1. The service (the app itself and its address)
gcloud run services delete mapnificently --region europe-north1

# 2. The stored images: find the repository, then delete it
gcloud artifacts repositories list
gcloud artifacts repositories delete cloud-run-source-deploy --location europe-north1   # `--source` deployments

# 3. The source copies kept by `--source` deployments (bucket named run-sources-…)
gcloud storage buckets list --format="value(name)" | grep run-sources
gcloud storage rm --recursive gs://BUCKET_NAME
```

Deleting the whole Google Cloud project removes all of it at once.

## Test the container locally

```bash
make docker-build     # the same image Cloud Run builds (~1 min, downloads the timetables)
make docker-run       # http://127.0.0.1:8080
make docker-test      # build, start and smoke-test: page, API, every city's data, a route
make docker-shell     # a shell inside the image, e.g. to run `mapnificently data status`
```

The container listens on `$PORT` (8080 by default, as on Cloud Run), runs as a non-root user
and keeps about 16 MB of prepared data; it uses about 160 MB of memory at rest.
