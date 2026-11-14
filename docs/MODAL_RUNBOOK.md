# Modal runbook

## Publish the measured event release

The main and PR preview workflows install the package pinned in `data/event_release.json` before deployment. The installer downloads the GitHub release asset, verifies the archive and every member, then publishes to the dedicated `nova-frontier-event-cases` Volume. The API reads this Volume through a read-only mount. Use the workflow smoke checks to verify the catalog digest, exact replays, case series and COG ranges.

For rollback, disable `NISAR_EVENT_RELEASE` in the deploy workflow to serve the existing `NISAR_CASES_PATH` directory, or restore the previous verified release metadata and redeploy through the same workflow.

## Stage and verify a case bundle

Build the prepared case directory with `python scripts/prepare_api_cases.py --output <directory>` after restoring release fixtures. Use a separate staging app and job Dict. Upload the generated directory to the staging Volume under a new `cases-...` name, then set `NISAR_CASES_PATH` to that name in the deploying shell. Defaults continue to read `/cases`. A versioned directory lets a later deployment switch the case set while keeping older files available.

Deploy a clean commit and compare `/health` and `/v2/capabilities` to the local Git SHA and normalized build fingerprint. The fingerprint covers shipped Python, schemas, model assets, pinned dependencies and the science explorer HTML. CRLF and LF text normalize to the same hash. Verify prepared PNG/COG assets, a measured job and its cache replay on staging before promotion.

### Rebuild a case bundle

Download the published bundle and the recorded exploration job you want to add from the Volume. Use a fresh local directory for each download. The packager checks the recorded manifest and each published asset against their SHA-256 digests.

```sh
mkdir -p case-input/base case-input/job
modal volume get --env main nova-frontier-chip-cache /cases case-input/base
modal volume get --env main nova-frontier-chip-cache /jobs/<job-id>/ case-input/job
python scripts/prepare_api_cases.py \
  --base-bundle case-input/base/cases \
  --san-marcos-explore case-input/job/<job-id> \
  --output cases-next
```

Verify the index, manifest, point series and PNG/COG checksums on a staging deployment. Upload the verified bundle to `/cases-next` in the Volume before merging the release PR, then point `NISAR_CASES_PATH` in the deployment workflow at it. To roll back the bundle, set `NISAR_CASES_PATH` in the workflow back to `cases` and deploy through the same reviewed workflow.

### Rebuild the bundle with Chocó

Restore the pinned measured fixtures using `scripts/download_examples.py`. Submit
an `insar-readiness` job with longitude `-76.2558`, latitude `4.8601`, dates
`2026-07-18` through `2026-08-23`, `product: "GUNW"`, and
`series_key: "148_A_004_2000_SH"`. The measured request point snaps to
`[-76.256, 4.86]`. Retrieve `manifest.json` and `correction_readiness_arrays.nc`
from the job directory in the Volume.

    python scripts/prepare_api_cases.py --output scratch/cases-next --choco-readiness <job-directory>
    python -m modal volume put --env main nova-frontier-chip-cache scratch/cases-next /cases-next

The builder checks the source array against the job manifest before reading it.
The deployment selects this directory using `NISAR_CASES_PATH`. Confirm the
deployed index, each manifest and series, and sample PNG hashes against the
corresponding `assets.json`. Run `python scripts/smoke_api.py <deployed-url> --require-choco`
after deployment to check the published Chocó contract. The existing `/cases` directory remains available
for rollback. Report figures displayed by the website require separate written
permission or a figure-specific license from the rights holder.

## Verify and rotate the Earthdata secret

The worker expects EARTHDATA_TOKEN in a Modal Secret. The gateway uses public catalog reads. The project default is the nova-frontier-earthdata secret. Enter the new value in Modal's Secret UI, or set NISAR_EARTHDATA_SECRET to a different resource name in the deployment shell, and redeploy. Run a bounded measured job, check its science_status and revoke the old token after verification. Keep token values out of Git, job JSON, terminal command arguments and logs. The local DPAPI provider is Windows-only.

## Configure the live GCOV reader and grid

The pull request preview uses `NISAR_GCOV_READER=pyfive` and `NISAR_GCOV_POSTING_M=40`.
The deployment defaults are `h5py` and the native grid. These settings apply only
to live `explore` jobs. They contribute to the service revision used for job reuse,
and pyfive uses a separate native chip cache. InSAR jobs and measured event replays
continue on their existing paths.

The archive reader fetches the same native window used by the control. Requests use
the Earthdata secret, bounded parallel byte ranges, a 128 MiB transfer budget per scene,
and a shared 150-second prefetch deadline. Failed reads are reported per scene. Change
the reader setting and redeploy to run the h5py control.

A 40 m job requires a verified 10 m L-band source grid. The worker averages each
4 × 4 block in linear gamma0 before converting to dB and requires all 16 native
samples to have valid support. The Data Lab reader uses a NaN-aware mean, which can
retain blocks with partial support. Each valid 40 m point value describes a block
average. Read the returned CRS, bounds, dimensions and valid counts with the values.
For a 384 × 384 native window, the measured 40 m grid is 96 × 96 over the same
3.84 km square. Display PNGs are capped separately at 256 pixels on the longest
side. The numerical grid remains available in the science response and raster artifacts.

The Modal preview workflow submits the five fixed Chocó granules from
[`scripts/modal_preview_choco.json`](../scripts/modal_preview_choco.json). It checks
the returned science grid and the PNG delivered by the gateway, and checks for
parallel archive batches or complete chip-cache reuse. See `science.gcov_reading`
for each scene's cache state, HTTP requests, bytes and range batches, and
`science.timings` for prefetch, aggregation and analysis. The smoke test reports
gateway elapsed time. Browser click-to-layer latency requires a separate measurement
through Explorer and the application backend.

## Check usage and limits

    python -m modal billing summary
    python -m modal app list
    python -m modal container list
    python -m modal app logs nova-frontier-model --env main

The public gateway has one container with a 0.25 physical-core CPU ceiling and 512 MiB memory ceiling. The worker has one CPU container and no GPU. One job can execute and one can queue. The service admits at most 12 new jobs per UTC day and five per client IP per day. A worker runs for at most 240 seconds. General traffic has a per-IP request limit of 120/minute. Published assets are bounded per file and the service allows up to 1 GiB of artifact transfer per UTC day. Limit state is in Modal Dict except for the gateway's minute-level IP counters. The cache is checked before new-work quotas. CORS governs browser origin and the service is public.

At list rates, a four-minute worker at 1 core and 2 GiB is approximately $0.0042 in reserved-resource compute. Even 12 fully timed-out jobs/day over a 31-day month would be about $1.57 on that model. A continuously active gateway at the stated CPU and RAM ceilings for 31 days would be about $11.75. These are conservative compute-only estimates before credits. Startup, image builds, idle windows, actual metering and other workspace workloads affect the bill. The Workspace usage limit is the outer billing control. Review it in Usage & Billing. [Pricing](https://modal.com/pricing) · [Budgets](https://modal.com/docs/guide/budgets)

The hosted mock at `/mock` uses the same gateway, queue and quotas as the API. The gateway and worker scale to zero between requests, with configured idle windows of 5 and 10 seconds. An idle deployment uses no compute after its containers stop. Page views, catalog reads, job polling and artifact requests can start the gateway. Storage and network egress are metered separately against the workspace allowances. [Modal autoscaling](https://modal.com/docs/guide/scale) · [Egress billing](https://modal.com/docs/guide/network-egress-billing)

The job record store is shared by gateway and workers. Deploy with one gateway and avoid overlapping deployments that share a state Dict while high traffic is admitted. Modal Dict entries expire after seven days without access. Completed results are eligible for a 24-hour cache hit. Artifacts and the chip cache remain in Volume storage until the operator cleans them up. Check usage periodically and retire old job and chip data during a maintenance window.

## Clear cache

Pause the app before removing live files or deleting the state Dict. Check the exact resources and keep the curated cases prefix.

    python -m modal volume ls --env main nova-frontier-chip-cache /
    python -m modal dict list --env main
    python -m modal app stop --env main nova-frontier-model
    python -m modal volume rm --help
    python -m modal dict delete --help

To clear stale scientific work, remove only /chips and /jobs from the configured Volume after confirming their names and delete the configured job-state Dict. Keep /cases. Deploy again and check the case index. Use a separate NISAR_APP_NAME, NISAR_STATE_DICT and NISAR_VOLUME when running a concurrent service.

## Failure triage

Use the job_id in app logs to correlate sanitized worker failures and the measured duration. HTTP 429 means a rate, delivery or job budget limit. HTTP 503 means queue capacity or scheduling failure. HTTP 502/504 on coverage means a catalog error. Check product entitlement and token expiry in Earthdata if archive reads fail. A coverage hit leaves quantitative support unchecked. A succeeded job can return a valid no_data result. Keep that distinction in operations and UI copy.
