# Measured NISAR event release

The selection uses measured L-band GCOV and GUNW for the tour and the free-click presets. The existing `Case` and `Job` response models remain the delivery interfaces. The companion `bundle.json` holds provenance and hashes, and `series.json` holds the server readouts. The nine-stop order is a teaching suggestion.

| Tour stop | Role | What the retained observations teach |
| --- | --- | --- |
| Qadirabad / Chenab, Pakistan | Development case | The marker brightened by +0.715 dB HH and +1.259 dB HV on July 26. The optical new-water group separately had 332 of 516 cells meeting the radar dual-darkening rule. The Qadirabad pilot uses a distinct marker and grid from the Chenab Transformer training cube. |
| Chókwè, Mozambique | Development case | 2,880 of 16,384 cells met the dual-darkening rule. 4,667 of 16,384 fell outside the observed baseline band on the sole assessment date. The optical comparison retained limited support and the radar inputs use the beta product. |
| Whitewater River / Richmond, Indiana | Development case | The 256 × 256 grid spans 10.24 km per side. The usable optical cells contained zero new-open-water cells, so the positive-group rate is null. |
| La Mojana / San Marcos, Colombia | Fresh evaluation | The window has 4,096 supported native radar cells. OPERA snapshots on September 24 and October 19 share 238 of 256 coarser comparison cells. The two grids and date brackets matter. |
| Sköllersta rye trial, Sweden | Held-out test | The retained median focal-versus-annulus HH shift was +1.78 dB across August 10–22. The documented August 18 trial date is inside that bracket. The parcel extent and field mechanism remain unresolved. |
| Bug Fire vicinity, Nevada | Held-out test | Repeated HV decline occurred in 127 of 1,737 supported cells inside the dated reference perimeter and 18 of 1,813 outside. The perimeter can enclose unburned cells. |
| High Plains, Kansas | Stable control | OPERA mapped zero confirmed vegetation-loss cells across 256 usable optical cells for the sampled bracket. The radar rule still flagged repeated HV decline in 79 of 4,096 native cells. Control refers to the off-event comparison. |
| Kumamoto, Japan | Development case | Three GUNW intervals bracket the reported event. Of 1,614 common phase cells, three met correction-input conditions. The contiguous chain has no closure check. |
| Chocó, Colombia | Development case | The two compatible GUNW pairs have coherence on all 3,844 cells. Common phase and correction-input support are zero. |

The free-click suite fixes the exact planned scene IDs, three-decimal point, UTC date bounds, native window size and lens. It includes **Qadirabad, Chókwè, Richmond, High Plains, Palouse, Matam and Rowe Creek**. Palouse adds a focal-versus-annulus comparison, Matam adds a Senegal River radar bracket, and Rowe Creek adds a disturbance case with ambiguous radar signatures. The three flood pilots use their historic prepared grids in the tour and native-resolution windows in their replays. The named populations, dates, denominators and bounds travel with each response. The planned replays use radar-only evaluation. The curated packets retain independent evidence where it can be tied to the reported comparison.

## Inventory and exclusions

`scratch/event-inventory-20261114/event_candidates.csv` is the comparison table. The JSON beside it retains exact granule IDs, dates, native grid metadata, reference details and source paths where those were available and parsed. Source paths are relative to the parent Documents folder. The inventory records source-specific roles. Hokkaido and Pantanal are the new Transformer geography tests. Amazon and Murray are development cases. Forest River, Deliblato, Chenab, Niscemi, Okavango, California, Mekong, Kafue and Iceland are training geographies. The frozen checkpoint lost to interpolation on both new tests. The Forest River DMD evaluation failed to improve on persistence on its two retained future dates. Neither model contributes to the published readouts.

The inventory includes earlier flood pilots and fresh evaluations, the Pursat holdout, Barh and Sköllersta Observatory outputs, model cases, InSAR readiness and network artifacts, and the predecessor demo assets. Venezuela has measured GUNW and blocked correction readiness. Omalos failed the independent reference geometry screen, and the polar point had no catalog coverage. The measured Nepal, European fire showcase, and Magdalena and Mexico City artifacts use older delivery contracts. The Mexico City apparent-LOS product requires separate review against the null validated-displacement policy. The older Data Lab includes both measured DMD runs and synthetic runs. The inventory gives a reason for each exclusion.

Sources inspected include the seven named project roots, 25 science branch refs visible before this work, and the current local science tree. `origin/main` was at `86be4db`, `origin/feat/modal-analysis-api` at `f222ec5`, and the local science tree at `2dc878e`. The original Modeling checkout at that SHA supplied the worker for replay generation before this port to NOVA. Some older artifact trees had inaccessible test or evidence folders. The inventory is bounded to the candidate registers, result files, chips and prepared artifacts found there. Some legacy records still have null or incomplete source metadata. Their linked sources remain the basis for further admission review.

## Reproduce and deploy

Run from the science repository root with its installed scientific dependencies and `PYTHONPATH=src`. For the original four release-based cases, `scripts/download_examples.py` verifies and restores the v0.5.0 measured fixture archive. The fixture-based `scripts/prepare_api_cases.py` renders from those inputs and can build Chocó and append San Marcos from measured job outputs. To recreate the delivered worker outputs, run the following from the original Modeling checkout at the source revision. The builder takes the retained six-case package, original Chocó job directory and legacy chips as inputs.

```powershell
$env:PYTHONPATH = 'src'
$env:OPENBLAS_NUM_THREADS = '1'
$env:OMP_NUM_THREADS = '1'
$env:MPLBACKEND = 'Agg'
$revision = python -c "import modal_app; print(modal_app.REVISION)"
.\.venv\Scripts\python.exe scripts/prepare_event_bundles.py `
  --prepared ../verify-san-marcos-release/cases-v8-20261113-san-marcos `
  --legacy 'C:/Users/danie/Documents/NISAC 3 - Copy' `
  --choco-source ../verify-choco-source/b2d15c0f87794b758f8becc603fc81f1 `
  --revision $revision --output scratch/cases-event-catalog-20261114
.\.venv\Scripts\python.exe scripts/verify_event_bundles.py scratch/cases-event-catalog-20261114
.\.venv\Scripts\python.exe scripts/inventory_event_candidates.py --documents 'C:/Users/danie/Documents' `
  --release scratch/cases-event-catalog-20261114 --output scratch/event-inventory-20261114
```

The builder validates measured chip sidecar hashes and checks each copied asset against its published SHA-256 before reusing it. The output name is immutable. Regenerating may change packaging bytes and job timing fields. The release checksum manifest pins the delivered bytes. The `modeling/scratch` directory is Git-ignored. The [NOVA GitHub release](https://github.com/NASA-NOVA-FRONTIER/nova-frontier-model/releases/tag/events-20261114) contains the immutable measured files. `data/event_release.json` pins the archive size, archive hash and checksum-manifest hash. The installer verifies every member before making the directory visible on the dedicated `nova-frontier-event-cases` Volume. The PR preview and main deployment workflows both provision that release and mount it read-only on their gateways.

```powershell
$env:PYTHONUTF8 = '1'
$env:NISAR_EVENT_RELEASE = '1'
modal run --env main scripts/install_event_release.py
```

The gateway serves the released cases and seven exact v2 replay results from the immutable Volume. Canonical replay identity uses the recorded release revision, so subsequent live worker updates preserve the original result and provenance. Ordinary jobs continue using the existing live revision, admission and cache. The prior `/cases` directory remains usable when the event release is disabled.

The release includes team-rendered layers and derived numeric products with NISAR and OPERA credits. Report figures, third-party imagery and original external geometry are excluded. Confirm licensing before distributing any of those originals. Preserve historical product versions as null where the retained metadata supplies only a composite release ID. Full original granule hashes are unavailable for range-read sources.

[Catalog schema](schemas/events.index.schema.json) · [Frontend handoff](FRONTEND_REQUESTS.md) · [Staging catalog](https://ch0comilo--nisar-event-catalog-staging.us-east.modal.run/v2/events.index.json)

### Replay and provenance scope

The stored replay jobs cover the exact catalog request bodies. Nearby clicks, different date windows, different chips, and requests that omit the fixed scene list need their own job. The Sköllersta and Bug Fire tour markers carry more than three decimal places. Their measured center windows are retained in the curated cases. A v2 cache replay would require matching chips for the rounded request instead of reusing those windows.

Matam's original OPERA comparison came from a separate aggregated admission grid. Rowe Creek has a retained OPERA DIST-ALERT-HLS reference dated September 23, 2026. The native replay packets for these two cases include radar only. The references are indexed for follow-up work. Richmond retains the original optics-derived groups and the complete measured series. Its optical null describes the usable reference window, and its full-window radar summary remains available.

The Git-tracked catalog and checksum manifest pin the staged release. Each per-case file map covers that case's `manifest.json`, `series.json`, asset allowlist and assets. The catalog pins the companion `bundle.json`, and the release checksum manifest pins the catalog and all case and job files. The checksum manifest itself has release SHA-256 `5e6ec7293e1d6553a5b008c4cd7ab3b422dad7d20bc6dfa89674f6a8e51b886b`.
