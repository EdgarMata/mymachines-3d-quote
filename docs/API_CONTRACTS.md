# Public API contracts (MM-005)

## Audit and consumers

The browser uses GET `/options`, POST `/analyze` and POST `/estimate`, through
Nginx `/api/` paths. Nginx removes that prefix. `/health` is used by local CI;
production Compose probes `/options`. `/` provides basic service identification.
`/upload` and `/slice` are legacy development/integration utilities, unused by the
current frontend. They remain implemented and tested; production disables their
registration by default rather than deleting their functionality.

| Method / path | Public success response | Production default |
|---|---|---|
| GET `/` | `name`, `status: ok`, `version` | Enabled |
| GET `/health` | `status: healthy` | Enabled |
| GET `/options` | `defaults`, material/quality catalogues and public machine options | Enabled |
| POST `/upload` | Sanitized `filename`, `size_bytes`, `geometry`, `status: analysed` | Disabled |
| POST `/slice` | Sanitized `filename`, `status: sliced`, public `slicing` statistics | Disabled |
| POST `/analyze` | `filename`, compatibility `status`, `orientation`, `machine_selection`, `display_machine` | Enabled |
| POST `/estimate` | `filename`, `quantity`, `material`, `quality`, quote `status`, `geometry`, `orientation`, `machine_selection`, `slicing`, `pricing`; review responses also have `message` | Enabled |
| GET `/docs`, `/docs/oauth2-redirect`, `/redoc`, `/openapi.json` | Generated Swagger/ReDoc/schema utilities | Disabled |

The audit found these previous exposures:

- Both slicing endpoints returned the service dictionary, including
  `material_cost_eur` and printer/process/filament profile filenames. Future
  diagnostic fields would also have crossed the boundary automatically.
- Machine selection/display results included `operational`, `profile_ready`,
  `automatic_quoting_ready` and `quoting_candidates_count`, exposing unnecessary
  operational readiness details. The frontend only needs public identification,
  volumes, quotation eligibility and compatibility results.
- Orientation removed one known path key with `pop`; that did not protect future
  internal fields. `/options` already selected most public fields, but copied
  nested volume dictionaries without a schema.
- Pricing already selected four public fields. Internal production/failure costs,
  rates and margins stay in the pricing service and must remain there.
- Automatic request validation could echo input values/context in error arrays.
  Uncaught failures (e.g. configuration reads) had no consistent JSON boundary.
- Swagger/ReDoc/OpenAPI and legacy endpoints had no production switch. CORS used
  local origins but unnecessarily allowed credentials and all methods/headers.

Frontend types contained unused `material_cost_eur`, `output_file` and candidate
count declarations. They are removed; no viewer, quote calculation or retry flow
is changed. The existing real-Orca machine regression now checks the selected
profile inside the service result, while confirming it is absent from HTTP.

## Response allowlists

`backend/app/api_models.py` defines Pydantic models for every ordinary success
response, including nested objects. FastAPI `response_model` validation projects
internal dictionaries onto these explicit fields. Unknown fields are ignored
recursively, not removed using a blacklist. Invalid/missing required fields or
non-finite public numeric values fail closed with a generic HTTP 500. Do not
bypass these models with raw success `Response`/`JSONResponse` objects in future
endpoints. Hand-written error responses contain only reviewed `detail` strings.

Nested public fields:

- `defaults`: `machine`, `material`, `quality`.
- Material options: `label`, `qualities`. Quality options: `label`,
  `layer_height_mm`. Machine options: `label`, `nozzle_diameter_mm`,
  `build_volume_mm`, `quoting_enabled`. Catalogue keys are public configured IDs.
- Dimensions/rotations: finite `x`, `y`, `z` numbers. Geometry: `dimensions_mm`,
  `volume_mm3`, `surface_area_mm2`, `vertices`, `faces`, `watertight`.
- Orientation: `status: oriented`, `rotation_degrees`, `dimensions_mm`,
  `bed_position.minimum_z_mm`. No output path.
- A selected/compatible/display machine: `id`, `label`, `build_volume_mm`,
  `quoting_enabled`. Machine selection: compatibility `status`,
  `model_dimensions_mm`, `default_machine`, nullable `selected_machine`,
  `compatible_machines`, `compatible_count`.
- Slicing: required finite `filament_g`, `print_time_seconds`; optional `status`,
  `slicer`, `machine_id`, `material`, `quality`, `filament_mm`, `filament_cm3`,
  `layers`, `print_time`. Optional statistics/metadata are omitted when not
  supplied, preserving existing service behavior. Slicing is per piece; quantity
  and the final order price remain separate. MM-002 still requires usable positive
  statistics before automatic pricing. The raw submitted MIME type is omitted
  from legacy upload responses because it is not needed by any consumer.
- Pricing: `machine`, `quantity`, `selling_price_eur`, `minimum_order_applied`.
  These are the existing values; no calculation, rounding, rate, profile or
  machine-selection algorithm changes are introduced.

Compatibility statuses remain `ready`, `technical_review_required` and
`model_exceeds_build_volume`. A successful quote uses `status: estimated`.
Both estimate review statuses return HTTP 200 with an application-defined English
`message` and explicit `slicing: null`, `pricing: null`; they do not trigger
slicing/pricing or become generic errors. Analysis preserves the existing fallback
display machine when no automatic selection exists.

Internal cost breakdowns, margins, maintenance/amortization/energy/labor/failure
costs, slicer commands, G-code, stdout/stderr, profile filenames, filesystem paths,
private readiness flags, arbitrary configuration and new internal fields are not
part of any response model. Internal service results retain what pricing and
selection require; filtering occurs at the HTTP boundary, including legacy routes.

## Error contracts and diagnostic policy

API errors use `{"detail": "English public message"}`. Existing domain/upload
messages and status codes 400/413/422/503/504 remain. Busy admission retains
`Retry-After: 5`; MM-001 reception limits and MM-003 cancellation/process ownership
are unchanged. Disabled/unmatched routes return 404; wrong methods return 405.

Request validation now returns HTTP 422 with the constant detail
`Invalid request parameters or missing STL file.` It does not return Pydantic
`input`, `ctx`, locations, error arrays or submitted values. This deliberately
trades per-field echo diagnostics for a bounded safe message; clients should
validate their own forms and use the existing explicit domain messages.

`api_errors.py` only passes through reviewed HTTP-exception messages. Parser or
unexpected HTTP details are replaced while preserving status. Unknown failures
before headers and invalid internal responses return a generic JSON 500.
Configuration/lifespan failures still prevent startup; exceptions after response
headers or client-disconnect send failures propagate normally. The pure ASGI
error boundary does not create another thread/task or change processing leases.

Diagnostics log registered route templates and exception class names, not raw
exceptions, tracebacks, request URLs/bodies, customer filenames, service results
or slicer output. This offers stage/type diagnostics without dumping geometry or
commercial data. The `InputValidationError` class is reserved for reviewed
application-defined public messages; never construct it from raw exception text.
HTTP error headers are not forwarded arbitrarily (the framework's 405 `Allow`
header is retained; generic HTTP 503 errors use the same safe `Retry-After: 5`
as processing admission). Nginx/outer proxies can still emit HTML transport errors,
and CORS preflight rejections retain Starlette's short English protocol response;
the existing frontend tolerates non-JSON errors.

Default server/proxy access logging can include client IPs and query strings.
No multipart/model body logging is added here. Deployment-specific access-log
redaction, retention and operator access need separate review; this is not a
promise that infrastructure logs never contain user-supplied metadata.

## Production surface and CORS

Settings are read at application import/startup; invalid settings fail startup
without echoing configured values. Production Compose now explicitly sets
`APP_ENV: production`; development Compose preserves development defaults.

| Variable | Development default | Production default |
|---|---|---|
| `APP_ENV` | `development` if unset | Set `production` explicitly |
| `ENABLE_LEGACY_ENDPOINTS` | `true` | `false` |
| `ENABLE_API_DOCS` | `true` | `false` |
| `CORS_ALLOWED_ORIGINS` | `http://localhost:5173,http://127.0.0.1:5173` | Empty |

`APP_ENV` accepts only those two values. Boolean overrides accept `true`/`false`
(case-insensitive). CORS accepts comma-separated explicit HTTP(S) origins without
credentials, query strings, fragments or non-root paths; wildcards are rejected.
Only GET/POST, Content-Type and Accept are allowed for cross-origin requests;
credentials are disabled. Same-origin Nginx requests do not need a CORS grant.
An empty production allowlist does not prevent same-origin use.

Legacy switches control registration, not just schema visibility. Disabled legacy
paths also do not acquire processing admission. Explicitly enabling legacy/docs
in production remains possible for controlled development/testing; all enabled
business endpoints still use the public response models. `/openapi.json` is not
served when docs are disabled, though internal code can still build a schema.
Hiding docs and restricting browser CORS are not authentication or authorization;
CLI clients can call enabled endpoints. Existing admission/byte/triangle/timeout
protections remain necessary, and no rate limiting or customer authentication is
introduced in MM-005.

## Verification and remaining limits

Tests inject synthetic private sentinels and unknown fields into nested internal
responses/configuration, exercise success/review/error/validation paths and
development/production settings, and preserve real binary/ASCII OrcaSlicer price
and selection regressions. Frontend build and error tests remain required.
Initial implementation verification passed all 133 backend tests in Docker (115
existing objectives and 18 new checks), all three frontend tests, the frontend production build,
Nginx syntax, both Compose files and whitespace checks. Native AMD64/ARM64 CI and
an approved real-browser deployment check remain separate verification.

Public machine labels, IDs, build volumes, geometry, quote statuses and prices
are intentional disclosures needed by the current viewer/quote flow. Compatibility
and review outcomes can imply availability; removing raw operational flags cannot
hide that necessary behavior. Public-labelled configuration fields must contain
public data. Example repository costs remain demonstration data; private
commercial configuration separation is MM-007, not provided by these schemas.
No claim is made about arbitrary data placed in a deliberately public label.
Filtering prevents direct disclosure of internal cost fields; it does not prevent
inference of costs, margins or pricing parameters by comparing public prices
across models, materials and quantities. The open-source pricing algorithm and
observable quote behavior are not a commercial-secrecy guarantee.

Rate limiting/abuse protection is MM-006. Other existing limitations remain:
single-process admission, no global geometry deadline or hard storage quota,
native-code/helper sandbox limitations and real deployment load validation. No
dependency, image publication, remote infrastructure change or deployment is required
by this local implementation.

## Final review and local functional verification

All seven registered ordinary routes declare public response models. Current
success paths return service dictionaries for projection; direct JSONResponse
paths contain only reviewed error messages. Do not return raw success Response
objects as a shortcut: FastAPI intentionally bypasses response models for them.
Additional regressions check model declarations, nested lists/null review objects,
omitted statistics and safe HTTP statuses/headers. Nullable optional print-time
and layer types match the TypeScript declarations; the UI design is unchanged.

Final review passed all 136 backend tests in Docker (133 preserved and three
additions), all three frontend tests, the frontend build, both Compose validations,
Nginx validation and diff/whitespace checks. The existing build bundle-size warning
remains. The nine opt-in HTTP checks also passed. They are separate from the
backend discovery count and are not automatically run by the current CI workflow.

The final review also runs the opt-in `backend/tests/nginx_smoke.py` separately
from unit discovery. It uses only in-memory synthetic STL and the existing
backend dependencies. Against an already running, isolated local production-mode
Compose instance with tests mounted read-only, run:

```sh
docker compose -p <review-project> -f docker-compose.yml -f <local-review-override> \
  run --rm --no-deps backend python /app/tests/nginx_smoke.py http://frontend
```

The local override must use distinct container names, existing local images,
read-only current backend source/tests and freshly built frontend assets/config,
`APP_ENV=production`, no backend host port and a loopback-only frontend port.
It is a disposable local setup, not an instruction to modify a deployment host.
The nine HTTP checks cover the SPA/bundle, options/health, analysis, manual
rotation parameters, actual PLA/PETG slicing/quotes, quality/quantity, both review
states, controlled errors, Nginx's 413 and concurrent 503/Retry-After. Production
legacy/docs paths are verified as 404. No browser rendering is exercised by them.

The session's browser-control host was unavailable. Real-browser functional
approval remains pending; HTTP checks do not validate WebGL, actual file-picker
interaction, rendered messages or button behavior. Before functional approval:

1. Open the local frontend in a real browser and confirm options load.
2. Upload a synthetic 10 × 20 × 30 mm STL box; confirm the viewer and display
   machine, rotate manually, and confirm dimensions/bed placement update.
3. Select PLA/standard and PETG/fine, change quantity, calculate quotes and check
   displayed price, time, weight and layers against the HTTP values.
4. Repeat with synthetic 300 mm and 1100 mm boxes; confirm technical review and
   build-volume incompatibility without a price.
5. Check readable upload/parameter/busy errors and manual retry after failure;
   confirm the browser console/network has no functional errors.

Do not mark visual validation complete until these interactions are observed.
