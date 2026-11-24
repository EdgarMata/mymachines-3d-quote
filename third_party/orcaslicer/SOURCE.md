# OrcaSlicer Source Code

## Component Information

- Project: OrcaSlicer
- Version: 2.4.2
- License: GNU Affero General Public License v3.0
- Upstream repository: https://github.com/OrcaSlicer/OrcaSlicer

## Corresponding Source

Upstream source code for version 2.4.2 is available at:

https://github.com/OrcaSlicer/OrcaSlicer/tree/v2.4.2

Release page:

https://github.com/OrcaSlicer/OrcaSlicer/releases/tag/v2.4.2

Source archive:

https://github.com/OrcaSlicer/OrcaSlicer/archive/refs/tags/v2.4.2.tar.gz

## Distributed Binaries

My-Instant 3D Quote uses the official upstream Linux AppImages without modifying the OrcaSlicer executable.

### AMD64 (x86-64)

Filename:

`OrcaSlicer_Linux_AppImage_Ubuntu2404_V2.4.2.AppImage`

SHA-256:

`d12fb8c8eac1aecd2dfb6377acd48f994f8fa439ed5292fa532dd82880f029fd`

### ARM64 (aarch64)

Filename:

`OrcaSlicer_Linux_AppImage_Ubuntu2404_aarch64_V2.4.2.AppImage`

SHA-256:

`e1a07275a25f176626c55a5df39e91bc4476d8c28ee4a3192ff758e29dd5c3ba`

## Build Instructions

The upstream Linux build script is available at:

https://github.com/OrcaSlicer/OrcaSlicer/blob/v2.4.2/build_linux.sh

Additional build documentation:

https://github.com/OrcaSlicer/OrcaSlicer/wiki/how_to_build_linux

## Integration

The backend Dockerfile downloads the official AppImage for the target architecture, verifies its SHA-256 checksum and extracts its contents.

MyMachines uses the OrcaSlicer command-line interface to estimate printing time and material consumption.

Custom slicer profiles are maintained separately in the `profiles/` directory.

## License

See `LICENSE.txt` in this directory for the original upstream license.

All upstream copyright notices and applicable third-party license terms remain in effect.
