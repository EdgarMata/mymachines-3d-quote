# Third-Party Notices

My-Instant 3D Quote uses and integrates third-party open-source software.

All third-party components remain subject to their respective copyright notices and license terms.

## OrcaSlicer

- **Project:** OrcaSlicer
- **Version:** 2.4.2
- **Copyright:** SoftFever and OrcaSlicer contributors
- **License:** GNU Affero General Public License v3.0
- **Repository:** https://github.com/OrcaSlicer/OrcaSlicer
- **Release:** https://github.com/OrcaSlicer/OrcaSlicer/releases/tag/v2.4.2
- **Source code:** https://github.com/OrcaSlicer/OrcaSlicer/tree/v2.4.2

My-Instant 3D Quote integrates OrcaSlicer through its command-line interface to estimate printing time and material consumption.

The backend Docker image is built using the official OrcaSlicer Linux AppImage. The downloaded binary is verified using a SHA-256 checksum.

### Slicer Profiles

This project includes printer, filament, and process configuration profiles compatible with OrcaSlicer.

Some profiles are based on or inherit settings from OrcaSlicer system presets and have been adapted for quotation purposes.

Original upstream contributions remain subject to their applicable licenses and copyright notices.

The MyMachines-specific profiles are intended for quotation and estimation purposes and are not validated production profiles.

## Other Dependencies

My-Instant 3D Quote also uses other open-source software, including:

- Python
- FastAPI
- Uvicorn
- Trimesh
- NumPy
- Node.js
- React
- Three.js
- React Three Fiber
- React Three Drei
- Vite
- TypeScript
- Nginx

These components remain subject to their respective licenses.

Dependency versions are defined in `backend/requirements.txt` and `frontend/package-lock.json`.

MyMachines gratefully acknowledges the work of all upstream developers and contributors.
