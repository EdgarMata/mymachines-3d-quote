# Commercial configuration (MM-007)

All bundled amounts are **demonstration values, not approved commercial prices**.
No actual MyMachines business costs are defined by this change. The AGPL source,
pricing algorithm and technical catalogue remain public. External configuration
keeps deployment data out of Git/images; it cannot prevent inference from quotes
or access by an authorized host/container operator.

## Audit and migration

Previously general costs were read from `config/pricing.json`, machine costs
from `config/machine_costs.json`, and material prices came from OrcaSlicer filament
profiles. The unused MATERIALS table in main.py disagreed with active PETG pricing
and listed unsupported ASA. It was not a quotation input and has been removed.

The two old commercial JSON files are replaced by one complete
`config/pricing.demo.json`. Existing values are preserved, including the actual
Orca prices of PLA 16 EUR/kg and PETG 20 EUR/kg, not the unused table's PETG value.
Public pricing machine names move to `pricing_name` in `config/printing.json`;
the names returned in quotations do not change. Selection criteria are unchanged.

| Existing input | New location and units | Existing interpretation |
|---|---|---|
| Currency | `currency`, EUR only | All monetary fields are EUR; no currency conversion |
| Material purchase price | `materials.<id>.cost_per_kg_eur` | Orca calculates/rounds material cost using actual filament consumption |
| Electricity tariff | `energy.electricity_cost_per_kwh_eur` | Multiplied by machine kW and print hours |
| Labour rate | `labour.cost_per_hour_eur` | Setup and postprocessing labour |
| Print failure | `risk.failure_rate_percent` | Failure-exposed material/machine/energy costs divided by `1 - rate/100`; fixed labour excluded |
| Target selling margin | `commercial.target_margin_percent` | Expected cost divided by `1 - margin/100`, **not** markup on cost |
| Minimum order | `commercial.minimum_order_eur` | Floor for the entire order |
| Purchase/residual value | `machines.<id>.{purchase_cost_eur,residual_value_eur}` | Depreciation uses `max(purchase - residual, 0)` |
| Lifetime | `machines.<id>.lifetime_hours` | Divisor for depreciation per print hour |
| Maintenance | `machines.<id>.maintenance_cost_per_hour_eur` | Maintenance per print hour |
| Average power | `machines.<id>.average_power_kw` | Existing energy-cost estimate, not a measured power curve |
| Preparation | `machines.<id>.setup_minutes` | One setup labour charge per order |
| Postprocessing | `machines.<id>.postprocess_minutes_per_piece` | Labour time multiplied by quantity |

No waste-rate component, separate machine hourly tariff or quantity discount
exists in the current formula. Depreciation, maintenance and energy derive its
machine hourly costs. Material/print hours scale linearly with quantity, setup
is fixed per order, and postprocessing is per piece. MM-002 still admits quantities
1 through MAX_QUANTITY (default 100); this safety setting is not commercial JSON.

Technical data stays public: density and diameter in filament profiles, temperatures,
flow, layer/infill/speed settings, printer volume/nozzle/profile/eligibility flags,
material/quality compatibility and public names in printing.json/profiles. Density
is not made private or changed. Average power and labour timings are cost-model
inputs in the external record even though they describe operating characteristics.
No technical printing profile or pricing formula changes in MM-007.

## Format and process snapshot

Use the complete public JSON as the schema example. All fields are required:
`schema_version: 1`, `configuration_kind`, `currency`, `energy`, `labour`, `risk`,
`commercial`, `machines` and `materials`. Configuration kind is `demonstration`
or `private`. There is no inheritance, partial override or merge with demo data.
Every configured material and machine, including currently disabled machines,
must have exactly one cost entry. New catalogue entries require matching costs.

The backend validates and caches one snapshot during application import/startup,
before any HTTP route is served. Pricing services use that snapshot. There is
no dynamic reload or reread of commercial values per request. A standalone
service lazily initializes the same cache; the HTTP application initializes it
before accepting requests.

For each slice, only filament_cost is overridden in a copy of the technical
profile, inside the owned MM-004 workspace. Orca still calculates the cost and
rounding; the engine consumes its material_cost_eur exactly as before. Shipped
profile prices remain demonstration metadata and cannot override the snapshot.
The generated profile follows immediate cleanup and lock-aware orphan recovery;
it can contain private cost data until actual removal. Original profiles remain
unchanged and no private price is passed as a command-line argument.

## Development and production

- Development without PRICING_CONFIG_PATH uses the bundled demonstration record.
- An explicitly set path in either environment must be an absolute readable
  regular JSON file. Empty, missing, invalid or unreadable selections fail;
  an explicit invalid path never falls back to demo.
- With APP_ENV=production, PRICING_CONFIG_PATH is mandatory, the record must say
  `configuration_kind: "private"`, and its resolved path must be outside the
  application/project roots. Symlinks to bundled configuration are rejected.
- Inside Docker this validates the container path, not the original host bind
  source. The operator must separately keep PRICING_CONFIG_HOST_PATH outside
  the checkout. Docker inspect/daemon diagnostics may disclose mount paths to
  authorized operators; path confidentiality is not guaranteed by API redaction.
- The production marker and external path require an explicit operator choice.
  They **cannot detect whether numbers are real or fictitious**. Synthetic tests
  deliberately use an external copy of demo values with the private marker.
- Production Compose sets the container path `/run/mymachines/pricing.json` and
  mounts PRICING_CONFIG_HOST_PATH read-only, with host-path creation disabled.
  The frontend receives no commercial mount or environment values.

Startup/CLI failures identify the category of configuration problem without
printing its path, submitted fields, numeric values or parser/Pydantic details.
CLI success prints only `Commercial configuration is valid.` Existing public
response allowlists continue to exclude internal cost structures, including
legacy responses; new fields do not become HTTP fields automatically.

## Validation policy

- JSON objects only; duplicate keys, invalid UTF-8, malformed JSON, NaN/Infinity,
  numeric overflow to infinity, unknown fields and unsupported schema/currency
  are rejected. Unknown keys are not echoed in errors.
- Numeric fields require real JSON numbers, not booleans, strings or nulls.
  Every number must be finite.
- Costs, residual/purchase values, power, minutes and minimum order are nonnegative.
  Zero prices/rates/times are allowed; there is no invented monetary ceiling.
- Lifetime is strictly positive to prevent division by zero. Failure and selling
  margin percentages are in `[0, 100)` because their existing divisors must stay
  positive. The upper bound is mathematical, not a recommended business margin.
- Residual above purchase retains the existing zero-depreciation floor; no new
  depreciation interpretation is introduced.
- Derived depreciation, electricity and labour unit-cost operations must remain
  finite. Finite inputs do not guarantee that quantity, duration and risk/margin
  calculations fit float arithmetic. Pricing explicitly checks intermediate
  hours, costs and the result before applying the minimum-order floor or returning
  a quote. Overflow/NaN produces a controlled HTTP 500 `Pricing failed.` without
  a price or internal values. This adds no arbitrary monetary ceiling; operators
  must still choose sensible units/values.
- Machine/material sets must exactly match the existing technical catalogue.
  No arbitrary machine, material, cost component or quantity rule is introduced.

## Preparing an operator file (future approved deployment)

Run these steps only on an explicitly authorized deployment host. Paths here are
recommendations, not details of an existing VPS installation. Keep the file and
backups outside the checkout, build context, frontend root and publicly served
directories. Restrict the directory to its operator (for example mode 0700) and
files to mode 0600. Match ownership to the actual backend UID; the current image
runs as root. Read-only mounts do not protect against privileged host access.

Create a private candidate from the public template, deliberately set
configuration_kind to private, and edit/approve every business value. Copying
the example or changing its marker does not turn demo values into approved prices.
Do not commit the result, pass individual values as environment variables or
copy it into a Dockerfile/build argument. The backend needs only one path variable.

For example, to create a new candidate without overwriting an existing file:

```sh
umask 077
COMMERCIAL_DIR=/srv/my-instant-quote-private
install -d -m 700 "$COMMERCIAL_DIR"
python3 - "$COMMERCIAL_DIR/pricing.candidate.json" <<'PY'
import json
from pathlib import Path
import sys
data = json.loads(Path("config/pricing.demo.json").read_text())
data["configuration_kind"] = "private"
with Path(sys.argv[1]).open("x", encoding="utf-8") as stream:
    json.dump(data, stream, indent=2)
    stream.write("\n")
PY
chmod 600 "$COMMERCIAL_DIR/pricing.candidate.json"
```

This is still a copy of demonstration numbers. Edit and approve it privately
before using it for actual commercial quotations. Nothing here claims to choose
appropriate prices for a business.

Once an MM-007-aware image is available and existing deployment variables are
set (including QUOTE_IMAGE_TAG and NGINX_TRUSTED_PROXIES_FILE), validate a candidate:

```sh
export PRICING_CONFIG_HOST_PATH=/srv/my-instant-quote-private/pricing.candidate.json
docker compose -f docker-compose.prod.yml config --quiet
docker compose -f docker-compose.prod.yml run --rm --no-deps --pull never \
  backend python -m app.services.commercial_config
```

The command uses the same strict production loader and read-only mount as startup.
An absent source is not created automatically. Operator files and real values are
not created by the MM-007 implementation or its tests.

## Updating and rolling back without rebuilding

1. Prepare and validate a complete candidate using the existing reviewed image.
   Keep a protected private backup of the previous validated configuration and
   record its compatible application version. Exclude backups from public Git,
   build inputs, CI artifacts and shared logs; review backup access/retention.
2. Use an approved maintenance window: prevent new quotations and let active
   jobs finish before replacing/recreating the backend. Do not mix deployments
   while active workers still use the previous snapshot.
3. Atomically replace the host file, or switch PRICING_CONFIG_HOST_PATH to the
   validated version. A single-file bind mount can retain the previous inode
   after host replacement. **Recreate** the backend; restart/reload alone is not
   sufficient for an atomically replaced bound file.
4. With the intended file selected, use the approved deployment command:

   ```sh
   docker compose -f docker-compose.prod.yml up -d --no-deps --force-recreate \
     --no-build --pull never backend
   docker compose -f docker-compose.prod.yml ps
   ```

   Confirm the existing backend healthcheck becomes healthy, check controlled
   startup diagnostics and verify a known synthetic quote before reopening access.
   No source commit or image rebuild is required for a schema-compatible price change.
5. For rollback, select/restore the last validated private file, validate it with
   its compatible MM-007-aware image, recreate backend and repeat health/quote
   checks. If validation fails, keep traffic closed; never enable silent demo
   fallback. Rolling back to software without this gate requires separate review.

Do not restart frontend or alter NPM merely to change prices. Its separate exact
proxy allowlist lifecycle remains documented in ABUSE_PROTECTION.md.

The automated local/CI Docker check exercises this sequence with synthetic prices
and real OrcaSlicer. It verifies that atomic replacement leaves the old mounted
inode and running snapshot unchanged until recreation, that only backend is
recreated, and that the immutable backend image ID remains identical for the
original quote, updated minimum order and rollback. Each new backend must become
healthy, the existing Nginx proxy must recover through its normal DNS refresh,
and rollback must reproduce the entire original public pricing object. This is
a schema-compatible configuration rollback test, not a VPS deployment test.

## Build/CI protection and verification

`.dockerignore` allows only explicit public build inputs, including the two
public config JSON files. Directory contents are re-excluded before individual
inputs are admitted, avoiding Docker's recursive directory-negation behavior.
New public source directories/assets/profiles must deliberately extend this list.
Backend Dockerfile copies the two public config files explicitly; the expensive
pinned Orca installation precedes application copies so source/config changes do
not invalidate it. Release/checksums and native architectures are unchanged.

`.gitignore` excludes *.private.json, *.local.json and private directories in
addition to existing environment/key exclusions. These guards do not stop someone
force-adding a file or editing a deliberately public allowed file. Keep real
data outside the repository regardless of filename. No history rewrite is made.

CI mounts only synthetic external fixtures at runtime. The Docker verification
script generates six recognizable JSON canaries under config, backend/app and
frontend/public, checks the **real Docker build context**, inspects bundled
runtime configuration, exercises failed/successful production startup/health,
runs the nine real-Orca HTTP tests with an external fixture, and checks the
same-image update/rollback above. Containers use a disposable internal Docker
bridge with no published host ports or external egress. Containers, network,
probe image and canaries are removed on ordinary completion/failure/signals. SIGKILL or
host failure still needs external teardown. No external test network,
Docker socket mounts, privileged mode, new secrets or dependencies are required.

The same checks run on both existing native AMD64/ARM64 CI jobs. SBOMs describe
images containing demo inputs only. The manual publication workflow builds from
the same restricted context and receives no private pricing mount/build argument;
it remains unexecuted. Local verification does not establish new native CI or
real deployment/browser approval. Subsequent opt-in local validation is documented
in [MM-008A](LOAD_AND_RESILIENCE_TESTING.md); it does not authorize deployment.
