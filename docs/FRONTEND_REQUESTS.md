# Frontend handoff for measured radar visualizations

1. Use `GET /v2/coverage` for a selected WGS84 point and UTC window, then `POST /v2/jobs` with `operation: "explore"`. Poll `status_url` and display queued, running and unavailable states from the API. Observation Plan dates are schedule information.
2. Use `result.visualizations` for a combined-view selector. Render title, actual dates, support counts, component labels and fixed dB ranges. Drape each `png_url` using its own WGS84 bounds. Keep numeric `cog_url` downloads and original `layers` available.
3. Use `GET /v2/cases/{id}` for prepared views. Richmond has measured HH/HV and VV/VH. Kumamoto has coherence, adjacent-pair coherence contrast and common phase support. Label any live wrapped phase in radians and keep LOS null.
4. Fix Qadirabad's marked-point sentence. At (73.713, 32.329) HH and HV increased +0.715 and +1.259 dB versus the baseline median. The area summary separately has 332/516 flagged new-water reference cells. Replace demo dates and bars with `diagnostic_band_timeline`. Use manifest bounds for Chókwè and Richmond.
5. Show `previews` as figures or animations with their supplied interpretation labels. Keep GP model spread, spatial quartiles and temporal MAD distinct. Display a null observation-noise field as unestimated.
6. Keep existing lens renderers working. When present, show `science.lens.evaluation` next to the current readout and `temporal_analysis` as a historical analysis panel. A masked point, an absent signature, an unavailable GP and missing optical evidence are different outcomes. For spectra, use the existing analysis operation and show the sampling-window curve and experimental status. [Lens and temporal evidence contract](API.md#lens-and-temporal-evidence)
7. Populate standalone analysis charts from `science.dashboard.series` and availability from `science.capabilities`. Add spectral and physical overlays from `result.layers`. Preserve relative asset paths and the original Modal job ID if relaying through a backend. [Dashboard field mapping](API.md#dashboard-data)

| Roadmap item | Science API handoff |
| --- | --- |
| Point or polygon | Current API takes points. Polygon sampling needs a separate AOI and resource contract with Data Lab. |
| Five months, five images | Use five months as a UI default. Point exploration uses up to five measured dates. Standalone analysis with spectral enabled uses up to six. Display the actual dates. |
| Ideal 10×10 km | Use the returned shape and posting. The audited 64×64 native 10 m request spans 640 m. Richmond's prepared grid spans 10.24 km. |
| L/S and mission switch | Current service publishes L-band products. Gate any future bands on measured availability and source metadata. |
| One minute | Prepared and cached routes are the judging path when time is limited. Show elapsed time and actual status for archive reads. |
| Downloads | PNGs, COGs and measured series are served by science. Globe screenshot and PDF assembly belong to the frontend. |

The Modal science explorer demonstrates the backend contract. The globe team can connect directly to the Modal API from the allowed browser origin, or Data Lab can relay the v2 responses and assets from its backend. Data Lab's `/api/analisis` currently has its own request and job result format. [API contract](API.md)

## Measured event catalog handoff

1. Fetch `GET /v2/events.index.json` from the science gateway and validate against [the catalog schema](schemas/events.index.schema.json). Use `tour_order` for the guided tour. A row with `replay: null` supports the curated view. A row with a replay record also offers an exact free-click shortcut. Resolve every relative URL against the same science origin.
2. Display each row's `role`, `teaches`, `status`, `coverage` and `evidence_state`. Keep sensor support and independent evidence separate. Describe High Plains as an off-event optical comparison in the stated bracket. The confirmed loss count applies to the 256 supported optical cells.
3. Read `manifest_url` through the existing v2 Case contract. Read `series_url` for the measured observations, group series, optional `diagnostic_band_timeline`, `point_readout` and `area_readout`. Show the server sentences, comparator, signed values, units, dates and denominator. The chip-wide series uses fixed common support. The flood signature and optical subgroup counts have their own masks and denominators. Preserve all fractions with their denominators. Use `bundle_url` for provenance, credits and per-file SHA-256.
4. Drape each layer's PNG on its own WGS84 `bounds`; use the matching COG for native values and native geometry. Use the catalog `grid` for the main window. Richmond spans 10.24 km on its 40 m grid. Optical support may use a different grid. Keep the published stretch across dates.
5. Send `replay.request` verbatim to `POST /v2/jobs`, including `scenes`, `window_pixels`, dates, point and lens. An exact replay returns HTTP 200 with `cache: "hit"`. Use `status_url` to poll and the returned layer URLs for assets. Requests outside the release continue through normal live admission.
6. Label the two Chókwè diagnostics separately. The dual-darkening rule flagged 2,880 of 16,384 cells. The baseline-band rule flagged 4,667 of 16,384. Show Richmond's optical new-water group as unavailable for positive-case comparison, with denominator zero and a null rate. For GUNW, show actual pair dates, coherence and phase support, correction gates and null LOS. The phase displays are wrapped relative-to-shared-pixel phase in radians. Keep forecasts, phenomenon class, cause and probability null.
7. Preserve acquisition uncertainty fields that are null as unestimated. Use the spatial IQR only to describe variation across measured cells. Display the Transformer and DMD policy from `model` if those methods are mentioned. The prepared readouts use measured statistics and computed lens diagnostics.

[Verified source staging catalog](https://ch0comilo--nisar-event-catalog-staging.us-east.modal.run/v2/events.index.json)
