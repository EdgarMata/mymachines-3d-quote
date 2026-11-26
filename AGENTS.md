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
See `docs/PROJECT_STATUS.md` and `docs/ROADMAP.md`. The first private ARM64 deployment and browser quote succeeded. MM-001 through MM-006 are committed; the maintainer confirms native AMD64/ARM64 CI. Consult `docs/TEMPORARY_STORAGE.md` for storage ownership and `docs/API_CONTRACTS.md` for public contracts. Real-browser functional approval remains pending because browser-control access was unavailable. MM-006 rate limiting and abuse protection are implemented and final-reviewed, without deployment; see `docs/ABUSE_PROTECTION.md` for proxy trust, required production configuration and remaining limits. MM-007 private commercial configuration is implemented and final-reviewed; source commit/push is authorized after mandatory checks, with no deployment; see `docs/COMMERCIAL_CONFIGURATION.md` for startup gates, public examples, private mounts and updates. Do not start MM-008 unless requested. No new frontend features or pricing formula changes now. Odoo integration is a later phase.
