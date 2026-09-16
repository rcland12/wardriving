# Printing the case

Tested on an **Elegoo Neptune 3 Pro** (0.4 mm nozzle, PLA) sliced in Cura. The
settings below aren't specific to that printer; start from your own tuned PLA profile
and change what's listed. All parts print **without supports** in the orientations
given.

Back to the [case guide](../README.md).

## Slicer settings

| Setting | Value | Why |
|---|---|---|
| Layer height | 0.2 mm | |
| Line width | 0.42 mm (**0.40 on the bezel**, see below) | |
| Wall line count | **4** | Most of a printed part's strength is in its walls |
| Top / bottom layers | 5 / 5 | |
| Infill | 25 % gyroid (40 % on arm and base, optional) | |
| Supports | **Off** | Every part is modelled to print without them |
| Nozzle / bed temperature | 205 °C / 60 °C | PLA; use what works for your filament |
| Print speed | 50 mm/s, outer walls 30 mm/s | Slower outer walls keep the teeth and holes crisp |
| Retraction | 0.8 mm at 45 mm/s | Direct drive; Bowden printers need more |
| Fan | 100 % from layer 2 | |
| Initial layer horizontal expansion | -0.1 mm | Reduces elephant's foot on bed-facing edges |

## Parts and orientation

| Part (`models/stl/wardriving-case-<part>.stl`) | Qty | Orientation on the bed | Brim | Override | Est. mass |
|---|---|---|---|---|---|
| `back` | 1 | As exported: back face down | optional | — | ~30–35 g |
| `bezel` | 1 | **Flip 180°**: front face down, button tabs and stylus tube pointing up | optional | **Line width 0.40 mm** | ~13–15 g |
| `tilt_plate` | **2** | As exported: teeth up | yes | **Infill 100 %** | ~10 g each |
| `arm` | 1 | Lying flat, teeth up, **rotated 45°** (246 mm won't fit straight) | yes | Infill 40 % (optional) | ~90–100 g |
| `base` | 1 | As exported: plate down, wall up | yes | Infill 40 % (optional) | ~45–55 g |
| `knob` | **2** | As exported: bolt-head pocket up | no | — | ~3 g each |
| `knuckle` | 1 | **Flip 180°**: the flat foot on the bed, upright rising from it | yes | — | ~15–18 g |

Total is roughly 190–220 g. Only two parts need reorienting, and both are a 180° flip:
the bezel and the knuckle.

### Why those orientations

- **The arm lies flat** so its layer lines run along the load path. Printed standing
  up, the layer boundaries at its base would sit in tension, which is where it would
  snap.
- **The bezel prints face-down** so the flex buttons rise as vertical walls and the
  wedge on each tab is a self-supporting 45° slope.
- **The knuckle prints foot-down.** It imports standing on the narrow end of its
  upright, with the foot cantilevered in the air, so flip it 180°. The foot then gives
  a 24 × 44 mm base, and the triangular brace slopes inward going up, so nothing
  overhangs. Its worst-case load is about 0.5 MPa across the layer lines, against
  PLA's roughly 20–40 MPa layer bond.
- **Teeth always point up.** Printed against the bed they'd pick up elephant's foot and
  mesh too tight.

### Per-part overrides

In Cura: right-click a model → Per Model Settings.

- **Bezel: line width 0.40 mm.** The flex buttons are 1.2 mm thick, which is exactly
  three lines at 0.40 but two lines plus gap fill at 0.42. This is the override that
  matters.
- **Tilt plate: infill 100 %.** Tightening the knob bends the middle of the plate
  between the nut and the tooth ring. At 25 % that 5.4 mm centre is two thin skins
  over sparse gyroid. Solid, it's far stiffer, for about 2 g more per plate.
- **Arm and base: infill 40 %.** Optional; they carry the load, but with 4 walls, 25 %
  is already adequate.
- **Brim on the tall and long parts.** The knuckle stands 44 mm tall and the arm is long
  and flat.

**The arm is 246 mm long** and won't lie straight on a 225 mm bed — it needs 253 mm with
a brim. Rotate it 45° in the slicer; diagonally it needs only 212 mm.

**The stylus tube** on the bezel prints as a plain vertical cylinder rising from the bed
alongside the button tabs, so it needs nothing special.

**The case back's 23 vent slots** are vertical, so each one only bridges its 3 mm width.
They need no supports. If a slot top sags slightly, it doesn't matter structurally.

## Suggested order

1. **Both tilt plates first.** They're small, and two of them mesh face to face, so you
   can check that the teeth engage and clamp at 10° steps before committing to big
   prints. **Rotate one half a tooth (5°) to mesh them.** Square-on, the teeth sit
   tip-to-tip and won't seat. That's normal, not a defect.
2. **The bezel.** It has the tightest tolerances in the project. Dry-fit it on the
   screen before printing anything else:
   - the glass should drop into its pocket without forcing
   - the two buttons on the bottom edge should flex and spring back
   - looking through the window, no plastic should cover the lit area
3. **The case back**, then the arm, base, knuckle and knobs in any order.

## After printing

1. **Form the threads.** Run an M3 screw slowly into each tapped hole and back it out:
   four in the case floor, four in the knuckle's upright, four in the base wall. This
   stops a screw from splitting a part during assembly.
2. **Press the nuts in:** four in the case-back ear pockets, and one each in the hex
   pockets on the knuckle's upright and the base wall, where the tilt plates' centres
   land. (The tilt plates have no nut pocket.) They should be snug; use a drop of CA
   glue if one is loose.
3. **Seat a bolt head in each knob.** The head slides 3 mm down a loose lead-in, then
   presses into a tight seat. Pull it home with a nut and washer on the thread, or warm
   the head briefly with a soldering iron and push it in.
4. **Check fit with the real hardware:** the microSD slit and the port openings, before
   final assembly.

Assembly continues in the [case guide](../README.md#assembly).

## Assumptions to check before printing the case back

- **The screen's two buttons are push-type.** If yours slide sideways instead, the flex
  tabs won't work.
- **Both power cords have the same USB-C plug size** (overmold 10.92 × 6.17 mm). A
  chunkier plug may not fit the openings; measure yours with calipers first.

## Exporting meshes (after changing the design)

In Fusion 360, export each body separately: right-click the body → **Save As Mesh** →
**STL (binary)**, units **mm**, refinement **High** (or custom: surface deviation
0.01 mm, normal deviation 5°). The refinement matters: the tilt joints have 36 teeth
about 1.3 mm wide and holes of Ø2.6–3.4 mm, and a coarse mesh turns those circles into
visible polygons so the teeth stop meshing cleanly.

Put the STLs in `models/stl/`, export STEP to `models/step/`, and regenerate the preview
with `python scripts/render_parts.py`. Sliced G-code is git-ignored, since it only works
on the printer and profile it was sliced for.
