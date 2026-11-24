# Slicer Profiles

This directory contains printer, filament, and process profiles used by My-Instant 3D Quote.

## Upstream Project

- Project: OrcaSlicer
- Repository: https://github.com/OrcaSlicer/OrcaSlicer
- License: GNU Affero General Public License v3.0
- Current runtime version: 2.4.2

## Profile Origins

The profiles in this directory include custom configurations and adaptations of OrcaSlicer system presets.

| Profile | Origin |
|---|---|
| `filaments/generic_pla.json` | Generic PLA preset |
| `filaments/mymachines_pla.json` | Adapted system preset |
| `filaments/mymachines_petg.json` | Adapted system preset |
| `printers/prusa_mk3s_0.4.json` | Prusa MK3S 0.4 mm preset |
| `printers/my_cloner_04.json` | Adapted Prusa MK3S preset |
| `processes/quality_0.20_mk3s.json` | OrcaSlicer MK3S Quality preset |
| `processes/draft.json` | Adapted system preset |
| `processes/standard.json` | Adapted system preset |
| `processes/fine.json` | Adapted system preset |

Some custom presets contain OrcaSlicer 2.3.1.0 export metadata. The exact original upstream revisions have not been fully established.

## Intended Use

These profiles are configured for automated printing time and material consumption estimation.

They are not certified or validated production profiles.

## Licensing

MyMachines modifications are released under AGPL-3.0.

Original upstream contributions remain subject to their applicable copyright and license terms.

See the root `LICENSE` and `THIRD_PARTY_NOTICES.md` files for additional information.
