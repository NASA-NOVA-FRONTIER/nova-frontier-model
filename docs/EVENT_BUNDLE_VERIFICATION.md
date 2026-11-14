# Event release verification

Release `cases-event-catalog-20261114` contains 12 measured case bundles, nine tour stops and seven cache jobs. The local verifier checked 1,209 files against SHA-256, validated the event catalog against its Draft 2020-12 schema and checked the assets against the original manifests and native arrays. Every advertised static numeric layer opened as a COG and matched its declared WGS84 extent. Total release size is 473,684,996 bytes.

| Regression | Verified result |
| --- | --- |
| Qadirabad marker, July 26 | +0.715138 dB HH and +1.259460 dB HV against the baseline median. 332/516 dual-darkening cells in the optical new-water group. |
| Qadirabad per-pass baseline-band counts | 1,458, 1,027, 1,123, 1,003 and 1,101 of 16,384 in date order. |
| Chókwè rules | 2,880/16,384 dual-darkening cells (17.58%) and 4,667/16,384 baseline-band departures (28.49%). The independent optical new-water group has 164/440 dual-darkening cells. |
| Richmond geometry and optical null | 256 × 256 at 40 m gives 10,240 m per side. The optical new-water denominator is zero and the rate is null. Baseline-band counts are 8,208, 6,062, 4,564 and 12,078 of 40,326 in date order. |
| Matched HH contrasts | Qadirabad −4.934324 dB across 463 matched pairs. Chókwè −4.344275 dB across 382. Richmond null. |
| Sköllersta | 29 focal and 593 annulus cells. August 10–22 HH/HV contrasts shifted +1.776311/+1.047028 dB. |
| Bug Fire | 155 cells had repeated HV declines across the full chip. Rerasterizing the source perimeter with the retained boundary exclusion reproduces 127/1,737 inside and 18/1,813 outside. |
| High Plains | The repeated-decline raster contains 79 of 4,096 flagged cells. The retained independent optical report lists zero confirmed vegetation-loss cells among 256 usable cells. |
| Kumamoto | 35,344 common coherence cells, 1,614 common phase cells and three common correction-input cells. Pair medians 0.55464, 0.38427, 0.47299. |
| Chocó | 3,844 common coherence cells. Pair medians 0.24716 and 0.19969. Zero common phase or correction-input cells. |

The readout verifier compares marker values, fixed-population medians, signed deltas, baselines, per-pass diagnostic counts, GUNW support, pair dates, wrapped phase rasters and replay scene/chip identity against numeric artifacts. The Bug Fire check uses the retained reference geometry locally. The output includes only the comparison statistics and source metadata.

The 177-test science suite passed offline. The repository audit and hosted endpoint checks are recorded below. The gross file hashes cover published derivatives. For range-read satellite data, the original granule name and local chip or pair hash support provenance. There is no SHA-256 for entire archive granules that were never downloaded.

The release retains null forecast, phenomenon class, probability, attribution, observation noise, model uncertainty and validated LOS fields. Any phase layer is in radians and uses the shared-reference convention with no applied corrections. Reviewed comparator wording identifies the measurement and relevant confounders. The supplied coverage and evidence scopes limit every claim to its observed population.

## Original hosted staging

The dedicated Modal Volume verified all 1,209 files after transfer. The isolated app then passed 100 HTTP requests against the same release. The verifier compared the catalog, checksum manifest, every curated manifest and series, one representative PNG/COG pair per case, and one pair per replay. The PNGs matched SHA-256. Every sampled COG returned HTTP 206 with the correct first bytes, native content length, ETag and range headers. The seven planned requests each returned two HTTP 200 cache hits with identical full result bodies, and every poll returned the same result. A deliberately different point returned HTTP 429 and scheduled no work.

| Hosted timing from this workstation | Result |
| --- | --- |
| First health call | 6,878.82 ms |
| 14 cache hits | 318.31–464.28 ms; median 397.61 ms |
| 84 subsequent GETs | 205.15–2,056.98 ms; median 559.02 ms; 72 below 1 second |
| Catalog / existing v2 case list | 1,093.77 / 2,056.98 ms |

These are end-to-end staging HTTP timings. They include network and Volume access. The warm replay target passed for every measured request. A strict first-visit or every-asset one-second target remains open. Frontend integration and browser timings belong to the frontend team's follow-up.

[Hosted catalog](https://ch0comilo--nisar-event-catalog-staging.us-east.modal.run/v2/events.index.json) · [Hosted checksum manifest](https://ch0comilo--nisar-event-catalog-staging.us-east.modal.run/v2/events/checksums.json)

To reproduce the hosted checks from the science checkout, set `PYTHONPATH=src` and run:

```powershell
.\.venv\Scripts\python.exe scripts/verify_event_bundles.py scratch/cases-event-catalog-20261114 `
  --legacy 'C:/Users/danie/Documents/NISAC 3 - Copy' `
  --url https://ch0comilo--nisar-event-catalog-staging.us-east.modal.run `
  --timings scratch/event-staging-timings.json --replay-only
```

The repository audit passed within the 10 MiB tracked-tree budget. Static bundles and job payloads live on the staging Volume and under the local ignored `scratch` directory. The catalog, checksum manifest, schema, scripts and handoff are the science branch changes.
