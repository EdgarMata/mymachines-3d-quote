# My-Instant 3D Quote — repository instructions

## Mission
Build a robust, maintainable, open-source FDM instant quotation service for MyMachines. Prioritize a working, secure MVP over new features. Public documentation, code, comments and commit messages use English; discuss work with the maintainer in European Portuguese. Explain important implementation decisions clearly, as the maintainer is learning programming.

## Working agreement
- Read `README.md` and the relevant files under `docs/` before starting. Inspect the actual repository and Git status; documentation is a snapshot, not a substitute for code.
- Preserve existing user changes. Make small, focused changes, run relevant tests, and report files changed, results and unresolved risks. Update project status when a milestone changes.
- You may edit local files and run local development/test commands within the granted permissions. Ask before committing, pushing, publishing packages, changing remote infrastructure, using SSH, deleting user data, or running destructive commands. Never force-push or expose credentials.
- Keep the public repository free of customer models, credentials, real commercial cost data, private operational details and generated output. Do not read or transmit secrets unnecessarily. Use synthetic test geometry.
- Local development and remote deployment are distinct. Do not mistake a local terminal for a VPS session. Never modify unrelated infrastructure or production services.
- Do not introduce new dependencies or architectural changes without explaining their need. Preserve the AGPL-3.0 license and applicable third-party notices.

## Engineering constraints
- React/TypeScript/Vite/Three.js frontend; FastAPI/Python/Trimesh backend; OrcaSlicer CLI for real FDM slicing; Docker Compose for deployment.
- Preserve native AMD64 and ARM64 builds, pinned OrcaSlicer release/checksums, and CI on both architectures.
- Preserve existing quotation behavior, orientation, machine selection and pricing regressions. Keep internal costs and filesystem details out of public responses. The current public repository uses demonstration pricing, not validated commercial prices.
- Prefer shared validation/service functions over duplicate endpoint logic. Bound uploads, parsing, slicing concurrency, execution time, temporary storage and response data. Treat all uploaded geometry and metadata as untrusted.
- Add regression tests for security and behavior changes. Run the existing test suite before and after changes; use the Docker backend when validating OrcaSlicer integration.
- Do not expose the current internal deployment publicly or execute the image publication workflow until the maintainer approves the relevant security and licensing gates.

## Current focus
See `docs/PROJECT_STATUS.md` and `docs/ROADMAP.md`. MM-001 through MM-007 are committed; the maintainer confirms successful native AMD64/ARM64 CI. The protected version is not deployed. Consult `docs/TEMPORARY_STORAGE.md`, `docs/API_CONTRACTS.md`, `docs/ABUSE_PROTECTION.md` and `docs/COMMERCIAL_CONFIGURATION.md` for the current security boundaries and remaining limits. MM-008A local validation is READY WITH CONDITIONS for a separately approved private validation, not public exposure; see `docs/LOAD_AND_RESILIENCE_TESTING.md`. The maintainer authorizes the reviewed MM-008A commit and SSH push after mandatory regressions pass. Keep operational metrics outside the checkout. Do not publish images, access the VPS or implement architectural protections in this review. Propose changes from measured evidence first. The prepared `docs/MM008B_PRIVATE_VALIDATION.md` runbook is not authorization to start MM-008B deployment/browser/NPM/shared-service work. No new frontend features or pricing formula changes. Odoo integration is a later phase.
