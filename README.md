# My-Instant 3D Quote

![License](https://img.shields.io/badge/License-AGPL--3.0-blue.svg)
![Status](https://img.shields.io/badge/Status-Active%20Development-orange)
![Python](https://img.shields.io/badge/Python-3.13-blue)
![React](https://img.shields.io/badge/React-19-blue)
![Docker](https://img.shields.io/badge/Docker-Ready-blue)

**An open-source, self-hosted instant quotation platform for 3D printing services.**

Developed by [MyMachines](https://my-machines.com).

## Overview

My-Instant 3D Quote is a web-based application designed to automate the quotation process for FDM 3D printing services.

Customers can upload a 3D model, inspect its geometry, define printing parameters, and receive an instant price estimate based on actual slicing data and configurable production costs.

The project aims to make professional 3D printing quotation tools accessible to makers, small workshops, print farms, and manufacturing businesses.

## Features

- STL file upload and geometry analysis
- Interactive 3D model visualization
- Manual model orientation
- Lay-on-face orientation
- Automatic printer selection
- Printer build volume validation
- Alternative printer selection based on availability
- Configurable materials and print quality profiles
- OrcaSlicer CLI integration
- Print time and filament consumption estimation
- Configurable production cost calculation
- Risk-adjusted pricing and commercial margins
- Minimum order value
- Separation of public pricing and internal production costs
- Docker-based deployment

## How It Works

```mermaid
flowchart TD
    A[Upload STL] --> B[Geometry Analysis]
    B --> C[Model Orientation]
    C --> D[Printer Selection]
    D --> E{Compatible Printer Available?}
    E -->|Yes| F[OrcaSlicer]
    E -->|No| G[Technical Review]
    F --> H[Time and Material Estimation]
    H --> I[Pricing Engine]
    I --> J[Instant Quote]
```

## Technology Stack

| Component           | Technology                |
| ------------------- | ------------------------- |
| Frontend            | React, TypeScript, Vite   |
| 3D visualization    | Three.js                  |
| Backend             | Python, FastAPI           |
| Geometry processing | Trimesh                   |
| Slicing engine      | OrcaSlicer                |
| Web server          | Nginx                     |
| Deployment          | Docker and Docker Compose |

## Architecture

```mermaid
flowchart LR
    A[Web Browser] --> B[Frontend]
    B --> C[FastAPI Backend]
    C --> D[Geometry Analysis]
    C --> E[Printer Selection]
    C --> F[OrcaSlicer]
    C --> G[Pricing Engine]
```

The frontend communicates with the backend through an HTTP API.

Internal production costs and pricing calculations remain on the backend. Only the necessary quotation information is returned to the customer.

## Getting Started

### Requirements

- Docker
- Docker Compose
- Git
- An AMD64 (x86-64) or ARM64 environment

### Installation

Clone the repository and enter the project directory.

```bash
git clone https://github.com/EdgarMata/mymachines-3d-quote.git
cd mymachines-3d-quote
```

Build and start the application:

```bash
docker compose up -d --build
```

Open the application in your browser:

```text
http://localhost:8080
```

The backend API documentation is available locally at:

```text
http://localhost:8000/docs
```

Production Compose disables the legacy upload/slice utilities and interactive API
documentation by default. The frontend continues to use `/options`, `/analyze`
and `/estimate` through Nginx. Public responses use explicit nested allowlists;
internal slicing costs, profile paths and operational flags stay server-side.
See [API contracts](docs/API_CONTRACTS.md) for response fields, errors, CORS and
development/production settings. These controls do not provide authentication.

To stop the application:

```bash
docker compose down
```

## Configuration

The quotation engine uses configurable JSON files.

| File                        | Purpose                                       |
| --------------------------- | --------------------------------------------- |
| `config/printing.json`      | Printers, materials and print quality options |
| `config/pricing.demo.json`  | Complete demonstration commercial configuration |
| `profiles/`                 | Slicer configuration profiles                 |

The included cost parameters are illustrative and are not approved commercial
prices. Development uses the complete demo by default. Production requires an
explicit external JSON selected with `PRICING_CONFIG_PATH`, marked `private`,
validated at startup and mounted read-only; there is no demo fallback. Prices
can be updated by controlled backend recreation without rebuilding images. See
[Commercial configuration](docs/COMMERCIAL_CONFIGURATION.md) for fields, units,
validation, migration and private deployment/backup procedures.

Backend safety limits default to 50 MiB per STL, 1,000,000 input triangles and
1–100 units per automatic estimate. STL geometry and printing parameters are
validated before slicing. Heavy endpoints admit one request at a time by default,
returning 503 / Retry-After 5 when busy; OrcaSlicer has a 60-second deadline.
These process-local protections require a single Uvicorn worker. Open/non-watertight models with usable finite surface
are accepted, but may still need repair or technical review. See
[Architecture](docs/ARCHITECTURE.md) for the acceptance policy and
[Deployment](docs/DEPLOYMENT.md) for environment settings and proxy limits.
Normal request cleanup runs immediately after processing; recognized abandoned
directories become eligible for recovery after one hour, checked at startup and
every five minutes. Active locks prevent age-based deletion of running jobs. See
[Temporary storage](docs/TEMPORARY_STORAGE.md) for ownership, privacy and limits.

Frontend Nginx adds per-IP request/active-connection limits, global budgets and
receive inactivity timeouts. Excess requests return safe HTTP 429 messages and
can be retried manually. Direct development trusts no forwarded client headers;
production requires an operator-owned exact proxy allowlist and verified NPM
header replacement. See [Abuse protection](docs/ABUSE_PROTECTION.md) before
configuring production or SSH-only internal access. Continuously slow uploads
and distributed attacks still require edge and operational controls.

## Project Structure

```text
mymachines-3d-quote/
├── backend/
├── frontend/
├── config/
├── profiles/
├── docker/
├── docs/
├── tests/
├── docker-compose.yml
├── LICENSE
└── README.md
```

## Roadmap

### Core quotation engine

- [x] STL geometry analysis
- [x] OrcaSlicer integration
- [x] Automatic printer selection
- [x] Configurable pricing engine
- [x] Public quotation response

### Web application

- [x] Interactive 3D viewer
- [x] Manual model orientation
- [x] Lay-on-face orientation
- [x] Instant quotation interface
- [x] Docker Compose deployment
- [x] Automated build/test CI on AMD64 and ARM64
- [ ] Automated deployment
- [ ] Production deployment
- [ ] Improved error handling and resource management

### Business integration

- [ ] Customer identification
- [ ] Quotation history
- [ ] Odoo CRM integration
- [ ] Odoo Sales integration
- [ ] Customer portal
- [ ] Online orders and payments

### Future improvements

- [ ] Asynchronous slicing jobs
- [ ] Multiple model quotations
- [ ] Batch printing optimization
- [ ] Additional 3D file formats
- [ ] Extended material support
- [ ] Production scheduling integration

## Project Status

This project is currently under active development.

The core quotation workflow has been validated locally, including geometry analysis, printer selection, slicing, pricing, and browser-based quotation.

It is not yet considered production-ready.

Generated quotations are estimates and may require technical review before manufacturing. Slicing results are intended for quotation purposes, not as validated production G-code.

## Contributing

Contributions, ideas, bug reports, and technical discussions are welcome.

Please use GitHub Issues to report bugs or propose new features.

The project values simplicity, reliability, maintainability, and reproducibility.

## License

This project is licensed under the **GNU Affero General Public License v3.0 (AGPL-3.0)**.

See the [LICENSE](LICENSE) file for details.

Third-party software and dependencies remain subject to their respective licenses.

## Acknowledgements

This project builds upon the work of several open-source communities, including:

- [OrcaSlicer](https://github.com/OrcaSlicer/OrcaSlicer)
- [FastAPI](https://fastapi.tiangolo.com/)
- [Trimesh](https://trimesh.org/)
- [Three.js](https://threejs.org/)
- [React](https://react.dev/)
- [Vite](https://vite.dev/)

## About MyMachines

MyMachines develops open-source machines, software, and tools for digital manufacturing.

Our goal is to make manufacturing technology more accessible, understandable, repairable, and reliable.

**It simply works.**

[my-machines.com](https://my-machines.com)
