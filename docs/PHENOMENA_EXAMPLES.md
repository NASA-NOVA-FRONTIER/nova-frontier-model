
# Measured phenomena examples

The organization audit found 12 prepared cases in the model gateway. The backend relays the gateway's case catalog and the frontend owns the tour and feature presentation. The expanded release restores measured examples from the local archives and gives the frontend five featured stops plus the full library. [Published 12-case release](https://github.com/NASA-NOVA-FRONTIER/nova-frontier-model/releases/tag/events-20261114) · [Frontend tour issue](https://github.com/NASA-NOVA-FRONTIER/nova-frontier-frontend/issues/28) · [Model feature issue](https://github.com/NASA-NOVA-FRONTIER/nova-frontier-model/issues/4)

## Case coverage

The library has 52 case windows, including 40 additions. Categories can share a case. Flood coverage includes distinct windows for the same event, reference comparisons and negative controls.

| Phenomenon | Related case windows | Featured stop or example |
| --- | ---: | --- |
| Earthquake | 3 | **Chocó** shows measured InSAR coherence and wrapped phase. |
| Wildfire | 9 | **Bug Fire** shows cross-polarization change and mapped reference support. |
| Deforestation context | 4 | **Pará event 02** shows a measured vegetation-disturbance signal. Forest conversion is unverified. |
| Flood | 22 | **La Mojana, San Marcos** shows measured flood signatures and optical comparison where supported. |
| Landslide | 1 | **Niscemi** shows the June–October measured aftermath of the January event. |
| Agriculture | 6 | Sköllersta, Räbelöv and Gotland show focal and annulus crop contrasts. |
| Volcano | 1 | Kīlauea shows one GUNW interval. The cause of the phase signal and the displacement are unestimated. |
| Wetland | 4 | Pantanal shows the measured scattering history. Pursat West adds a dated optical comparison. |
| Subsidence context | 1 | Mexico City shows one GUNW interval. The corrected displacement and vertical rate are unestimated. |
| Ice | 0 | The audited sources contain no verified ice-targeted scene. The worker still lacks GOFF support. |

Prepared cases exercise the current agriculture, flood/wetland, vegetation-disturbance and wildfire lenses, plus the InSAR workflow. The catalog uses `observed_dynamics` for descriptive radar views, including Niscemi. The measurements are L-band. [Phenomenon definitions](../contracts/phenomena-catalog.json) · [S-band tracking](https://github.com/NASA-NOVA-FRONTIER/nova-frontier-model/issues/9)

## Model features

| Evidence in the prepared series | Example |
| --- | --- |
| Physical summaries and temporal and spatial spectra | Pantanal and Hokkaido |
| Historical Gaussian-process fit and frozen Transformer comparison | Pantanal and Hokkaido |
| Baseline and assessment fingerprint | Wheatbelt |
| Flood analog score and optical comparison | Pursat West |
| Historical DMD result with persistence benchmark | Forest River |
| Historical UMAP study scope and validation limits | Pantanal |

The forecasts and cause fields are null. The Forest River DMD experiment used a separate crop and persistence had lower RMSE. The Pursat West analog is an experimental score. A null uncertainty field means unestimated.

For the showcase, read `tour_order` from `GET /v2/events.index.json`. Use `phenomena` for the full library and unavailable themes and `feature_examples` to link to the Feature Lab examples. Read the selected case's `series_url` and use its `model_features` and artifact URLs. Retain each case's dates, reference scope, support denominator and null fields. The seven existing exact replay requests are still available. [Frontend handoff](FRONTEND_REQUESTS.md)

## Local source audit

| Folder | Evidence consulted |
| --- | --- |
| NISAR PROJECT | Older Kīlauea and Nepal GCOV browse records and additional coordinate-only browse windows. |
| NISAR 4 | Current baseline and the existing compiled event release, lens contracts and measured example records. |
| NISAC Preview Quota | Documentation and retained results checked against the older sources. |
| NISAC Modal Visualizations | Visualization artifacts and overlapping measured cubes. |
| NISAC Main | Documentation and retained lens results checked against the older sources. |
| NISAC 3 - Copy | Measured radar cubes, holdout chip series, saved flood admissions, interferogram reductions and regional model experiments. |
| NISAC 2 | Legacy wildfire gallery, Brazilian land-change windows and Mexico City InSAR artifacts. |

The extension reads the immutable baseline and the retained inputs in `NISAC 3 - Copy` and `NISAC 2`. It publishes measured renderings, native COGs, diagnostic series, source metadata and hashes. Browse-only candidates and unverified coordinate windows were kept outside the case library.

## Build and verify locally

Run from the model checkout with its scientific dependencies installed. Use the baseline archive from the published 12-case release or its verified unpacked copy. Set `$documents` to the folder containing the seven original project folders.

```powershell
$env:PYTHONPATH = "src"
$baseCases = "path/to/unpacked/cases-event-catalog-20261114"
$releaseDir = "scratch/cases-event-catalog-20261115-r1"
$releaseZip = "scratch/nisar-phenomena-20261115-r1.zip"
python scripts/extend_event_release.py --base $baseCases --documents $documents --output $releaseDir
python scripts/verify_event_bundles.py $releaseDir --legacy (Join-Path $documents "NISAC 3 - Copy")
python scripts/package_event_release.py $releaseDir $releaseZip
```

The generated archive and its deployment pin are local candidate artifacts under Git-ignored `scratch`. The candidate pin records the manifest hash, ZIP digest and size, with a null asset URL pending publication. The existing production pin still points at the 12-case archive. Once the new ZIP is published as a release asset, its URL and candidate pin can be supplied to the existing deployment workflow. [Event release process](EVENT_BUNDLES.md)
