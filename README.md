# TinyPedal Broadcast — Features (Updated)

This repository contains the broadcast/spectator UI with the following updated features implemented on top of the base reader API.

Screenshot: ![sample](images/sample.png)

## Key updated features

- **Chequered flag vehicle status**
  - Vehicles that have finished the race are shown with a `CHEQUERED` status tag.
  - `CHEQUERED` overrides all other status tags for that vehicle unless the vehicle is in the pits.
  - If a vehicle is in the pits it shows `PIT`, which takes precedence over `CHEQUERED`.

- **Pos Change column (class-relative)**
  - New `Pos Change` column (placed between `Last Laptime` and `Vehicle Integrity`) shows how many positions a driver has gained or lost relative to their class starting grid position.
  - Displays a green up arrow `? N` for positions gained, red down arrow `? N` for positions lost, `-` when unchanged, and `--` when grid information is not available.
  - There is a small gap between the arrow and the count for readability (eg. `? 3`).
  - Calculation is class-relative — starting grid positions are inferred per-class from `api.read.vehicle.qualification()` and converted to a class grid rank.

- **Vehicle Integrity colouring**
  - Vehicle integrity percentage is coloured according to new thresholds:
    - 100% — green
    - below 50% — red
    - below 87% (strictly) — orange
    - otherwise — yellow

## Where to look in the code

- Primary UI and logic for these features are in `tinypedal/ui/broadcast_view.py`.
- Sample image: `images/sample.png` (included in this repo).

## Quick start

1. Ensure the environment and reader API are available for the connected simulator.
2. Run the application entry point (example): `python run.py`.
3. Enable broadcast/spectator mode in the UI to see the updated driver list and the new columns.

If you need the README to include installation steps, examples of configuration values, or a different image, tell me what to add.
