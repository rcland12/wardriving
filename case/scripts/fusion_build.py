#!/usr/bin/env python3
"""Canonical, reproducible source for the wardriving-case Fusion model.

This file is the SINGLE SOURCE OF TRUTH for the geometry. Nothing is typed
ad-hoc into Fusion; every feature comes from here.

Each stage is a block of Fusion 360 API Python. The blocks are executed inside
Fusion by an add-in that accepts scripts over HTTP (not part of this repo;
scripts/push.py sends them). Print a stage's code with:

    python scripts/fusion_build.py params   # print the code for a stage
    python scripts/fusion_build.py 01
    ...

Stages are deliberately small so no single call approaches a 120 s script
timeout.

    python scripts/fusion_build.py --list    # show all stages
    python scripts/fusion_build.py --all     # every stage, in order

Design notes that this script encodes (learned the hard way):
  * every stage is wrapped in `def build():` because the bridge execs in a
    function scope and nested defs cannot see module-level names
  * Join via participantBodies does NOT merge -> always extrude NEW + Combine
  * Combine invalidates body references -> always re-fetch by name
  * a name collision makes Fusion silently rename to "foo (1)", after which
    every by-name lookup targets the WRONG body -> mk() asserts the name it got
  * teeth are patterned, not sketched 36x, or the sketch solver stalls
"""
import sys

GAP = 24.0

# name -> (min-corner x, min-corner y) in mm. Fixed, deterministic, and
# verified non-overlapping by the `verify` stage.
SLOTS = {
    "01_case_back":  (0.0,   0.0),
    "02_case_bezel": (0.0, 100.0),
    "03_tilt_plate": (0.0, 200.0),
    "04_arm":        (0.0, 260.0),
    "05_base":       (0.0, 320.0),
    "06_knob":       (200.0, 200.0),
    "07_knuckle":    (60.0, 200.0),
}

# ---------------------------------------------------------------- parameters
# (name, expression, unit, comment).  Order matters: derived come last.
PARAMS = [
    ("pi_len", "85 mm", "mm", "Pi PCB long edge"),
    ("pi_wid", "56 mm", "mm", "Pi PCB short edge"),
    ("pi_pcb_t", "1.4 mm", "mm", "Pi PCB thickness"),
    ("pi_hole_d", "2.75 mm", "mm", "Pi mount hole dia"),
    ("pi_hole_inset", "3.5 mm", "mm", "hole inset from both edges"),
    ("pi_hole_span_l", "58 mm", "mm", "hole span along length"),
    ("pi_hole_span_w", "49 mm", "mm", "hole span along width"),
    ("pi_top_comp_h", "15.6 mm", "mm", "tallest top component (USB-A stack)"),
    ("pi_under_comp_h", "2 mm", "mm", "solder tails under PCB"),
    ("usbc_ctr", "11.2 mm", "mm", "USB-C centre from origin corner"),
    ("usbc_w", "9.5 mm", "mm", "USB-C opening w"),
    ("usbc_h", "3.6 mm", "mm", "USB-C opening h"),
    ("hdmi0_ctr", "26 mm", "mm", "micro-HDMI0 centre"),
    ("hdmi1_ctr", "39.5 mm", "mm", "micro-HDMI1 centre"),
    ("hdmi_w", "7 mm", "mm", "micro-HDMI opening w"),
    ("hdmi_h", "3.2 mm", "mm", "micro-HDMI opening h"),
    ("av_ctr", "54 mm", "mm", "AV jack centre"),
    ("av_d", "6.6 mm", "mm", "AV jack dia"),
    ("eth_ctr", "45.75 mm", "mm", "ethernet centre - Pi4 puts it at the GPIO end (photos)"),
    ("eth_w", "16 mm", "mm", "ethernet opening w"),
    ("eth_h", "13.5 mm", "mm", "ethernet opening h"),
    ("usb3_ctr", "27 mm", "mm", "USB3 (blue, middle) pair centre"),
    ("usb2_ctr", "9 mm", "mm", "USB2 pair centre - nearest the power edge"),
    ("usb_w", "15 mm", "mm", "USB-A pair opening w"),
    ("usb_h", "16 mm", "mm", "USB-A pair opening h"),
    ("usb_plug_w", "16.5 mm", "mm", "MEASURED plug overmold width"),
    ("usb_plug_h", "9 mm", "mm", "MEASURED plug overmold height"),
    ("usbc_plug_w", "10.92 mm", "mm", "MEASURED Pi power cable overmold width"),
    ("usbc_plug_h", "6.17 mm", "mm", "MEASURED Pi power cable overmold height"),
    ("adp_a", "21.33 mm", "mm", "MEASURED HDMI adapter near side, from origin"),
    ("adp_b", "40.2 mm", "mm", "MEASURED HDMI adapter far side, from origin"),
    ("adp_out", "10.34 mm", "mm", "MEASURED adapter reach past Pi pcb edge"),
    ("adp_below", "0 mm", "mm", "measured body_h puts the adapter's low point ~9.25, above the floor"),
    ("adp_hood", "1", "", "1 = enclose the HDMI adapter in a local hood, 0 = open notch"),
    ("adp_clr", "0.6 mm", "mm", "clearance around the HDMI adapter (was 1.0 at the sides)"),
    ("lcd_usbc_ctr", "10.97 mm", "mm", "MEASURED LCD USB-C centre, left edge, from origin"),
    ("lcd_usbc_plug_w", "10.92 mm", "mm", "ASSUMED same cord as the Pi power cable"),
    ("lcd_usbc_plug_h", "6.17 mm", "mm", "ASSUMED same cord as the Pi power cable"),
    ("lcd_usbc_below", "1.8 mm", "mm", "LCD USB-C centre below LCD pcb (open_h 3.43 / 2)"),
    ("fpc_relief", "1.5 mm", "mm", "touch-panel flex wraps the right edge of the glass"),
    ("edge_clear_b", "2.5 mm", "mm", "extra room on power edge: USB-C/AV protrude past pcb"),
    ("edge_clear_r", "3 mm", "mm", "extra room on USB edge: USB-A/ethernet protrude"),
    ("usb3_open", "0", "", "1 = open the blue USB3 stack (merged into one notch if USB2 is open too)"),
    ("usb2_open", "1", "", "1 = open the black USB2 stack - where the GPS and Wi-Fi adapter live"),
    ("btn_pwr_a", "3.78 mm", "mm", "MEASURED PWR button near side, bottom edge, from origin"),
    ("btn_pwr_b", "6.59 mm", "mm", "MEASURED PWR button far side"),
    ("btn_bkl_a", "14 mm", "mm", "MEASURED BKL button near side"),
    ("btn_bkl_b", "17.94 mm", "mm", "MEASURED BKL button far side"),
    ("btn_pwr_ctr", "(btn_pwr_a + btn_pwr_b) / 2", "mm", "PWR button centre"),
    ("btn_bkl_ctr", "(btn_bkl_a + btn_bkl_b) / 2", "mm", "BKL button centre"),
    ("sw_below", "2 mm", "mm", "MEASURED button centre below the LCD pcb back face"),
    ("sw_out", "0.63 mm", "mm", "MEASURED button tip past the LCD pcb edge"),
    ("tab_w", "5 mm", "mm", "flex button tab width"),
    ("tab_t", "1.2 mm", "mm", "flex button tab thickness (flush with the case outside)"),
    ("tab_slit", "0.6 mm", "mm", "slit either side of a flex tab"),
    ("tab_cav", "2.4 mm", "mm", "clearance cavity behind a flex tab"),
    ("tab_root_skin", "1.2 mm", "mm", "bezel face left above the tab hinge"),
    ("nub_h", "2.5 mm", "mm", "nub height at the button"),
    ("nub_gap", "0.3 mm", "mm", "nub to button at rest"),
    ("tab_press", "0.8 mm", "mm", "design deflection at the nub when pressed"),
    ("gasket", "0.5 mm", "mm", "compressed foam between bezel lip and glass border"),
    ("sd_ctr", "28 mm", "mm", "microSD centre on short edge"),
    ("sd_w", "14 mm", "mm", "microSD slot width"),
    ("sd_below", "1.6 mm", "mm", "SD slot drop below PCB"),
    ("lcd_len", "85 mm", "mm", "LCD pcb length"),
    ("lcd_wid", "56 mm", "mm", "LCD pcb width"),
    ("lcd_pcb_t", "1.6 mm", "mm", "LCD pcb thickness"),
    ("lcd_active_w", "73.44 mm", "mm", "active area w"),
    ("lcd_active_h", "48.96 mm", "mm", "active area h"),
    ("lcd_active_off_x", "2.52 mm", "mm", "MEASURED lit-area offset x"),
    ("lcd_active_off_y", "3.78 mm", "mm", "MEASURED lit-area offset y"),
    ("lcd_panel_h", "5.4 mm", "mm", "DERIVED: total 24.4 - pi 1.4 - gap 16 - lcd 1.6"),
    ("glass_w", "84.13 mm", "mm", "MEASURED glass slab width (covers ~whole LCD board)"),
    ("glass_h", "54.82 mm", "mm", "MEASURED glass slab height"),
    ("glass_off_x", "0 mm", "mm", "MEASURED glass offset x"),
    ("glass_off_y", "0 mm", "mm", "MEASURED glass offset y"),
    ("stack_gap", "16 mm", "mm", "MEASURED Pi pcb top to LCD pcb bottom"),
    ("fit_port", "0.6 mm", "mm", "clearance around port openings"),
    ("fit_board", "0.4 mm", "mm", "clearance around PCB edges"),
    ("wall", "2.4 mm", "mm", "wall thickness"),
    ("floor_t", "6 mm", "mm", "floor thickness (NB: 'floor' is reserved)"),
    ("hole_comp", "0.2 mm", "mm", "hole diameter compensation"),
    ("bezel_grip", "2 mm", "mm", "bezel lip overlap onto LCD pcb"),
    ("bezel_face_t", "2 mm", "mm", "bezel front face thickness"),
    ("m3_clear", "3.4 mm", "mm", "M3 clearance hole"),
    ("m3_tap", "2.6 mm", "mm", "M3 self-tap into PLA"),
    ("m3_head_d", "5.5 mm", "mm", "M3 head diameter"),
    ("m3_nut_af", "5.5 mm", "mm", "M3 nut across flats"),
    ("m3_nut_t", "2.4 mm", "mm", "M3 nut thickness"),
    ("m3_head_h", "3 mm", "mm", "M3 socket head height"),
    ("bezel_screw_l", "30 mm", "mm", "M3 screw length the ear nut pockets are sized for"),
    ("kn_case_screw_l", "8 mm", "mm", "M3 screws, knuckle foot to case back"),
    ("kn_plate_screw_l", "8 mm", "mm", "M3 screws, tilt plate to knuckle upright"),
    ("base_plate_screw_l", "12 mm", "mm", "M3 screws, base tilt plate to base wall"),
    ("tilt_bolt_l", "30 mm", "mm", "M3 socket-head tilt bolts (longest on hand)"),
    ("tilt_washer_t", "0.6 mm", "mm", "flat washer between each knob and the arm"),
    ("case_tap_d", "5 mm", "mm", "tapped depth in the case floor (floor is floor_t)"),
    ("base_tap_d", "10 mm", "mm", "tapped depth in the base wall (was 8; deepened for M3 x 12)"),
    ("peg_d", "2.5 mm", "mm", "Pi locating peg dia"),
    ("peg_h", "3 mm", "mm", "Pi locating peg height"),
    ("standoff_d", "6 mm", "mm", "Pi standoff boss dia"),
    ("ear_d", "10 mm", "mm", "corner ear boss dia"),
    ("spine_w", "36 mm", "mm", "solid back spine width"),
    ("frame_w", "10 mm", "mm", "back perimeter frame width"),
    ("tilt_teeth", "36", "", "tooth count = 10deg steps"),
    ("tooth_half", "2.0 deg", "deg", "half angular width of an ARM tooth (was 2.2)"),
    ("plate_tooth_half", "1.85 deg", "deg", "half width of a PLATE tooth - narrower so new plates mesh with an already-printed arm too"),
    ("tooth_relief_r", "12.2 mm", "mm", "plate centre relief radius: clears a printed arm's fused inner ring"),
    ("tooth_relief_d", "0.6 mm", "mm", "plate centre relief depth"),
    ("tilt_hub_d", "34 mm", "mm", "toothed hub diameter"),
    ("tilt_tooth_h", "1.8 mm", "mm", "tooth depth"),
    ("tilt_r_in", "13.5 mm", "mm", "tooth ring inner radius (was 7: unprintable; 13.5 keeps plate teeth >= 2 extrusions)"),
    ("tilt_plate_t", "6 mm", "mm", "tilt plate base thickness"),
    ("tp_bolt_dx", "14 mm", "mm", "tilt plate bolt spacing x/2"),
    ("tp_bolt_dy", "19 mm", "mm", "tilt plate bolt spacing y/2"),
    ("pad_w", "36 mm", "mm", "tilt plate pad width"),
    ("pad_l", "44 mm", "mm", "tilt plate pad length"),
    ("arm_h", "206.2 mm", "mm", "pivot to pivot (was 130; +3 in per user 2026-09-13)"),
    ("arm_w", "40 mm", "mm", "arm width"),
    ("arm_t", "14 mm", "mm", "arm thickness"),
    ("base_l", "90 mm", "mm", "base footprint length"),
    ("base_w", "80 mm", "mm", "base footprint width"),
    ("base_t", "5 mm", "mm", "base plate thickness"),
    ("base_pivot_z", "arm_w / 2 + 3 mm", "mm", "base pivot above plate top: arm end radius + 3mm (was 20 = touching)"),
    ("wall_x", "48 mm", "mm", "base wall width, fore-aft"),
    ("wall_t_top", "10 mm", "mm", "base wall thickness at top"),
    ("wall_t_bot", "24 mm", "mm", "base wall thickness at bottom"),
    ("wall_h", "44 mm", "mm", "base wall height above plate top"),
    ("knob_d", "24 mm", "mm", "tilt knob diameter"),
    ("knob_t", "10 mm", "mm", "tilt knob thickness"),
    ("knob_floor_t", "3.9 mm", "mm", "knob under the bolt head (was 6.9: head sunk 3mm so an M3 x 30 reaches the nut behind the plate)"),
    ("tilt_tip_relief_d", "6 mm", "mm", "bolt-tip hole depth in the base wall behind the nut pocket (the knuckle's goes through)"),
    ("kn_fb_t", "8 mm", "mm", "knuckle upright thickness"),
    ("kn_fa_l", "16 mm", "mm", "knuckle foot length behind the upright"),
    ("kn_fa_t", "6 mm", "mm", "knuckle foot thickness"),
    ("kn_w", "44 mm", "mm", "knuckle width (tilt plate long side)"),
    ("kn_h", "arm_w / 2 + 3 mm", "mm", "top joint axis behind the case back: arm end radius + 3mm"),
    ("kn_sx1", "4 mm", "mm", "foot screw row 1, from the upright's back face"),
    ("kn_sx2", "12 mm", "mm", "foot screw row 2"),
    ("kn_sy", "15 mm", "mm", "foot screws half-spacing along the case height"),
    ("kn_gusset_ang", "30 deg", "deg", "gusset slope off the foot (was 45: row-1 screws sat 9mm down their tunnels)"),
    # derived
    ("case_in_l", "pi_len + 2 * fit_board + edge_clear_r", "mm", "inner pocket length"),
    ("case_in_w", "pi_wid + 2 * fit_board + edge_clear_b", "mm", "inner pocket width"),
    ("case_out_l", "case_in_l + 2 * wall", "mm", "outer shell length"),
    ("case_out_w", "case_in_w + 2 * wall", "mm", "outer shell width"),
    ("stack_total", "pi_pcb_t + stack_gap + lcd_pcb_t + lcd_panel_h", "mm", "sandwich"),
    ("pcb_top_z", "floor_t + pi_under_comp_h + pi_pcb_t", "mm", "Pi PCB top from shell back"),
    ("rim_h", "floor_t + pi_under_comp_h + pi_pcb_t + stack_gap", "mm", "back shell rim height"),
    ("bezel_h", "lcd_pcb_t + lcd_panel_h + gasket + bezel_face_t", "mm", "bezel total height"),
    ("tilt_r_out", "tilt_hub_d / 2", "mm", "tooth ring outer radius"),
    ("tilt_step", "360 deg / tilt_teeth", "deg", "angle per tooth"),
    ("yoke_off", "tilt_plate_t + tilt_tooth_h + arm_t / 2", "mm", "face to arm centreline"),
    ("tilt_nut_pocket_d", "m3_nut_t + 0.3 mm", "mm", "tilt nut pocket depth, in the knuckle upright and base wall behind each plate"),
    ("ear_nut_d", "rim_h + bezel_h - 3.2 mm - (bezel_screw_l - 1 mm) + m3_nut_t", "mm",
     "ear nut pocket depth: an M3 x bezel_screw_l ends 1mm past the nut"),
    ("stylus_d", "4.88 mm", "mm", "MEASURED stylus barrel diameter (2026-09-13; was 5.0)"),
    ("stylus_len", "110 mm", "mm", "ASSUMED stylus length (unmeasured, conservative) - clearance checks only"),
    ("stylus_bore", "5.2 mm", "mm", "tube bore: prints ~5.0, snug slip fit on the 4.88 barrel (was 5.4: loose)"),
    ("stylus_wall", "2 mm", "mm", "tube wall"),
    ("stylus_tube_l", "22 mm", "mm", "tube length back from the bezel front face"),
    ("stylus_x", "-(stylus_bore / 2 + stylus_wall + 0.8 mm)", "mm", "tube centre, case x: just outside the left wall, 0.8mm clear"),
    ("stylus_y", "case_out_w - 10 mm", "mm", "tube centre, case y: top-left, below the corner ear"),
    ("vent_w", "3 mm", "mm", "vent slot width (vertical slots: bridges only this when printing)"),
    ("vent_rib", "3 mm", "mm", "minimum solid rib between slots and around any existing opening"),
    ("vent_band", "2 mm", "mm", "solid band kept above the floor and below the rim"),
    ("vent_corner", "8 mm", "mm", "solid wall kept at each corner (ears carry the bezel screws)"),
]

HELPERS = r'''
import adsk.core, adsk.fusion, math, traceback
app = adsk.core.Application.get()
des = adsk.fusion.Design.cast(app.activeProduct)
root = des.rootComponent
UP = des.userParameters
FO = adsk.fusion.FeatureOperations
NEW, CUT = FO.NewBodyFeatureOperation, FO.CutFeatureOperation

def P(n):
    p = UP.itemByName(n)
    if p is None: raise RuntimeError('missing parameter: ' + n)
    return p.value * 10.0

def PD(n): return math.degrees(UP.itemByName(n).value)
def c(m): return m / 10.0
def vr(m): return adsk.core.ValueInput.createByReal(c(m))
def pt(x, y, z=0): return adsk.core.Point3D.create(c(x), c(y), c(z))

def bod(nm):
    b = root.bRepBodies.itemByName(nm)
    if b is None: raise RuntimeError('no body named ' + nm)
    return b

def newsk(): return root.sketches.add(root.xYConstructionPlane)
def newxz(): return root.sketches.add(root.xZConstructionPlane)
def newyz(): return root.sketches.add(root.yZConstructionPlane)
def rect(sk,x0,y0,x1,y1): sk.sketchCurves.sketchLines.addTwoPointRectangle(pt(x0,y0),pt(x1,y1))
def circ(sk,cx,cy,d): sk.sketchCurves.sketchCircles.addByCenterRadius(pt(cx,cy),c(d/2.0))

def hexa(sk,cx,cy,af,a0=30.0):
    """Hexagon across flats af; a0 = angle of the first vertex (30 puts one on sketch v, 0 on u)."""
    r = af / math.sqrt(3.0); L = sk.sketchCurves.sketchLines
    ps=[pt(cx+r*math.cos(math.radians(60*i+a0)), cy+r*math.sin(math.radians(60*i+a0))) for i in range(6)]
    for i in range(6): L.addByTwoPoints(ps[i], ps[(i+1)%6])

def profs(sk):
    oc = adsk.core.ObjectCollection.create()
    for p in sk.profiles: oc.add(p)
    if oc.count == 0: raise RuntimeError('sketch produced no profiles')
    return oc

def _ext(sk, z0, h, op, target=None):
    f = root.features.extrudeFeatures
    inp = f.createInput(profs(sk), op)
    if abs(z0) > 1e-9:
        inp.startExtent = adsk.fusion.OffsetStartDefinition.create(vr(z0))
    inp.setDistanceExtent(False, vr(h))
    if target is not None: inp.participantBodies = [target]
    return f.add(inp)

def mk(sk, z0, h, name):
    """New body, asserting Fusion did not silently rename it on collision."""
    if root.bRepBodies.itemByName(name) is not None:
        raise RuntimeError('body already exists, refusing to shadow: ' + name)
    r = _ext(sk, z0, h, NEW)
    b = r.bodies.item(0); b.name = name
    if b.name != name:
        raise RuntimeError('rename collision: asked %s got %s' % (name, b.name))
    return b

def cut(nm, sk, z0, h): _ext(sk, z0, h, CUT, bod(nm))

def _merge(nm, newbodies, op=None):
    tools = adsk.core.ObjectCollection.create()
    for b in newbodies: tools.add(b)
    ci = root.features.combineFeatures.createInput(bod(nm), tools)
    ci.operation = FO.JoinFeatureOperation if op is None else op
    ci.isKeepToolBodies = False
    root.features.combineFeatures.add(ci)

def addto(nm, sk, z0, h): _merge(nm, list(_ext(sk, z0, h, NEW).bodies))

def addtosym(nm, sk, dist):
    f = root.features.extrudeFeatures
    inp = f.createInput(profs(sk), NEW)
    inp.setSymmetricExtent(vr(dist), True)
    _merge(nm, list(f.add(inp).bodies))

def teeth(nm, cx, cy, z0, h, half=None):
    """One tooth at the origin, circular-patterned about the built-in Z axis,
    then moved to (cx, cy). Parametric designs refuse free construction axes
    ('Environment is not supported'), and 36 sketched sectors stall the solver."""
    n = int(round(UP.itemByName('tilt_teeth').value))
    if half is None: half = PD('tooth_half')
    ri = P('tilt_r_in'); ro = P('tilt_r_out')
    step = 360.0/n
    tw = math.radians(2*half)*ri; gw = math.radians(step-2*half)*ri
    if tw < 0.84 or gw < 0.84:
        raise RuntimeError('teeth unprintable at r_in=%.1f: tooth %.2fmm, gap %.2fmm '
                           '(need >= 0.84mm = two 0.42 extrusions)' % (ri, tw, gw))
    before = set(b.name for b in root.bRepBodies)
    a0 = math.radians(-half); a1 = math.radians(half)
    sk = newsk(); L = sk.sketchCurves.sketchLines; A = sk.sketchCurves.sketchArcs
    p1 = pt(ri*math.cos(a0), ri*math.sin(a0)); p2 = pt(ro*math.cos(a0), ro*math.sin(a0))
    p3 = pt(ro*math.cos(a1), ro*math.sin(a1)); p4 = pt(ri*math.cos(a1), ri*math.sin(a1))
    L.addByTwoPoints(p1, p2); A.addByThreePoints(p2, pt(ro, 0), p3)
    L.addByTwoPoints(p3, p4); A.addByThreePoints(p4, pt(ri, 0), p1)
    tooth = _ext(sk, z0, h, NEW).bodies.item(0)
    oc = adsk.core.ObjectCollection.create(); oc.add(tooth)
    ci = root.features.circularPatternFeatures.createInput(oc, root.zConstructionAxis)
    ci.quantity = adsk.core.ValueInput.createByReal(n)
    ci.totalAngle = adsk.core.ValueInput.createByString('360 deg')
    ci.isSymmetric = False
    root.features.circularPatternFeatures.add(ci)
    fresh = [b for b in root.bRepBodies if b.name not in before]
    if len(fresh) != n:
        raise RuntimeError('expected %d teeth, got %d' % (n, len(fresh)))
    if abs(cx) > 1e-9 or abs(cy) > 1e-9:
        oc = adsk.core.ObjectCollection.create()
        for b in fresh: oc.add(b)
        mf = root.features.moveFeatures; mi = mf.createInput2(oc)
        m = adsk.core.Matrix3D.create()
        m.translation = adsk.core.Vector3D.create(c(cx), c(cy), 0)
        mi.defineAsFreeMove(m); mf.add(mi)
        fresh = [b for b in root.bRepBodies if b.name not in before]   # refs stale after move
    _merge(nm, fresh)
    return n

def place(nm, sx, sy):
    """Move body so its bbox min corner lands exactly on (sx, sy). Deterministic."""
    b = bod(nm); bb = b.boundingBox
    dx = sx - bb.minPoint.x * 10.0
    dy = sy - bb.minPoint.y * 10.0
    oc = adsk.core.ObjectCollection.create(); oc.add(b)
    mf = root.features.moveFeatures; inp = mf.createInput2(oc)
    m = adsk.core.Matrix3D.create()
    m.translation = adsk.core.Vector3D.create(c(dx), c(dy), 0)
    inp.defineAsFreeMove(m); mf.add(inp)

def report(nm):
    b = bod(nm); bb = b.boundingBox
    return {'name': nm, 'vol_cm3': round(b.volume, 2), 'valid': b.isValid,
            'bbox': [round((bb.maxPoint.x-bb.minPoint.x)*10,1),
                     round((bb.maxPoint.y-bb.minPoint.y)*10,1),
                     round((bb.maxPoint.z-bb.minPoint.z)*10,1)],
            'min': [round(bb.minPoint.x*10,1), round(bb.minPoint.y*10,1)],
            'bodies_total': root.bRepBodies.count}

def probe(pl, u, v):
    s = root.sketches.add(pl); circ(s, u, v, 4)
    f = root.features.extrudeFeatures; i = f.createInput(profs(s), NEW)
    i.setSymmetricExtent(vr(4), True); b = f.add(i).bodies.item(0); bb = b.boundingBox
    r = [(bb.minPoint.x+bb.maxPoint.x)/2*10, (bb.minPoint.y+bb.maxPoint.y)/2*10,
         (bb.minPoint.z+bb.maxPoint.z)/2*10]
    b.deleteMe(); return r

def planemap(pl):
    """Which model axis each sketch axis lands on, with sign. On the YZ plane
    Fusion swaps them (sketch u -> model -z, v -> +y); assuming no swap once
    laid the base wall down flat."""
    cen = probe(pl, 10, 20)
    iu = min(range(3), key=lambda k: abs(abs(cen[k])-10))
    iv = min(range(3), key=lambda k: abs(abs(cen[k])-20))
    if iu == iv: raise RuntimeError('ambiguous plane probe %s' % cen)
    return (iu, 1.0 if cen[iu] > 0 else -1.0, iv, 1.0 if cen[iv] > 0 else -1.0)

def skp(mp, x, y, z):
    m = (x, y, z); iu, su, iv, sv = mp
    return m[iu]*su, m[iv]*sv
'''

# Derived locals shared by the geometry stages. Kept OUT of HELPERS because the
# `params` stage runs on an empty document where these parameters don't exist.
SHARED = r'''
OL=P('case_out_l'); OW=P('case_out_w'); W=P('wall'); FT=P('floor_t'); RH=P('rim_h')
PX=W+P('fit_board'); PY=W+P('fit_board')+P('edge_clear_b'); PCT=P('pcb_top_z'); PT=P('pi_pcb_t'); FP=P('fit_port')
ED=P('ear_d'); EO=2.5
EARS=[(-EO,-EO),(OL+EO,-EO),(-EO,OW+EO),(OL+EO,OW+EO)]
BX=P('tp_bolt_dx'); BY=P('tp_bolt_dy')
def tabgeom(key):
    """(button centre, tab centre, nub x0, nub x1), case x. A tab shifts away
    from the adapter hood if it would cut into its wall; the nub stays on the
    button, clipped to the tab. Refuses if the nub can't cover the actuator."""
    bc=PX+P(key); tw_=P('tab_w'); ts_=P('tab_slit')
    hood_x=PX+P('adp_a')-P('adp_clr')-W          # hood's outer wall face
    tc=min(bc, hood_x-ts_-tw_/2.0)
    n0=max(bc-(tw_-1.0)/2.0, tc-tw_/2.0); n1=min(bc+(tw_-1.0)/2.0, tc+tw_/2.0)
    if n0 > bc-0.75 or n1 < bc+0.75:
        raise RuntimeError('nub cannot cover %s actuator: %.2f..%.2f vs centre %.2f' % (key,n0,n1,bc))
    return bc,tc,n0,n1
'''

STAGES = {}

STAGES["newdoc"] = r'''
# ONLY adds a document. Closing the doc this call runs in breaks the add-in,
# which still holds a reference to it after the script returns.
d = app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)
return {'created': d.name}
'''

STAGES["closescratch"] = r'''
# Close never-saved "Untitled" docs this script made, EXCEPT the active one.
# Saved designs (e.g. the user's files) are never touched.
act = app.activeDocument
closed, kept = [], []
for dd in list(app.documents):
    if dd.name.startswith('Untitled') and not dd.isSaved and dd != act:
        closed.append(dd.name); dd.close(False)
    else:
        kept.append(dd.name)
return {'closed': closed, 'open_now': kept, 'active': act.name}
'''

STAGES["01"] = r'''
N='01_case_back'
sk=newsk(); rect(sk,0,0,OL,OW); mk(sk,0,RH,N)
sk=newsk()
for x,y in EARS: circ(sk,x,y,ED)
addto(N,sk,0,RH)
sk=newsk(); rect(sk,W,W,OL-W,OW-W); cut(N,sk,FT,RH-FT+1)
SPW=P('spine_w'); FW=P('frame_w')
sk=newsk(); rect(sk,FW,FW,OL/2-SPW/2,OW-FW); rect(sk,OL/2+SPW/2,FW,OL-FW,OW-FW)
cut(N,sk,-1,FT+2)
HX=[PX+P('pi_hole_inset'), PX+P('pi_hole_inset')+P('pi_hole_span_l')]
HY=[PY+P('pi_hole_inset'), PY+P('pi_hole_inset')+P('pi_hole_span_w')]
sk=newsk()
for x in HX:
    for y in HY: circ(sk,x,y,P('standoff_d'))
addto(N,sk,FT,P('pi_under_comp_h'))
sk=newsk()
for x in HX:
    for y in HY: circ(sk,x,y,P('peg_d'))
addto(N,sk,FT+P('pi_under_comp_h'),P('peg_h'))
z0=PCT-0.5
# Pi USB-C power: size the hole for the cable overmold, not the bare port
cw=max(P('usbc_w'),P('usbc_plug_w'))+2*FP; ch=max(P('usbc_h'),P('usbc_plug_h'))+2*FP
czc=PCT+P('usbc_h')/2.0; cx=PX+P('usbc_ctr')
sk=newsk(); rect(sk,cx-cw/2,-1,cx+cw/2,W+1); cut(N,sk,czc-ch/2,ch)
# HDMI adapter (rigid U-bridge) reaches adp_out past the Pi edge, well beyond
# the normal wall. adp_hood=1: a local hood on the bottom face encloses it (the
# bezel carries a matching cap); adp_hood=0: open notch, adapter pokes out.
ax0=PX+P('adp_a')-P('adp_clr'); ax1=PX+P('adp_b')+P('adp_clr'); yh0=PY-P('adp_out')-P('adp_clr')
if UP.itemByName('adp_hood').value > 0.5:
    sk=newsk(); rect(sk,ax0-W,yh0-W,ax1+W,W); addto(N,sk,0,RH)
    sk=newsk(); rect(sk,ax0,yh0,ax1,W+1); cut(N,sk,FT,RH-FT+1)
else:
    sk=newsk(); rect(sk,ax0,-1,ax1,W+1); cut(N,sk,FT,RH-FT+1)
# USB-A: the black USB2 stack only (GPS + antenna both fit in one stack, and a
# USB2 port can't run SuperSpeed signalling next to a 2.4GHz radio).
# Opening both stacks gives ONE merged notch: they sit 18mm apart and a 17.7mm
# opening each would leave a 0.3mm sliver of wall between them.
uw=max(P('usb_w'),P('usb_plug_w'))+2*FP
ctrs=[P(k) for k,fl in (('usb3_ctr','usb3_open'),('usb2_ctr','usb2_open'))
      if UP.itemByName(fl).value > 0.5]
if not ctrs: raise RuntimeError('no USB stack opened: set usb2_open or usb3_open')
u0=PY+min(ctrs)-uw/2; u1=PY+max(ctrs)+uw/2
sk=newsk(); rect(sk,OL-W-1,max(W,u0),OL+1,u1); cut(N,sk,z0,RH-z0+1)
# LCD's own USB-C (left edge, on the Pi-facing side of the LCD board): open-top
# notch high in the left wall, just under the LCD pcb
lw=P('lcd_usbc_plug_w')+2*FP; lcy=PY+P('lcd_usbc_ctr')
lz0=RH-P('lcd_usbc_below')-P('lcd_usbc_plug_h')/2.0-FP
sk=newsk(); rect(sk,-1,lcy-lw/2,W+1,lcy+lw/2); cut(N,sk,lz0,RH-lz0+1)
# Flex-button notches: the bezel's flex tabs hang past the rim into these
# open-top notches, so their nubs can reach the LCD buttons.
tw=P('tab_w'); tsl=P('tab_slit'); zbt=-(P('sw_below')+P('nub_h')/2.0+0.8)   # tab bottom, bezel coords
for key in ('btn_pwr_ctr','btn_bkl_ctr'):
    bc,tc,n0,n1=tabgeom(key); sk=newsk(); rect(sk,tc-tw/2-tsl,-1,tc+tw/2+tsl,W+1)
    cut(N,sk,RH+zbt-0.4,-zbt+1.4)
sdc=PY+P('sd_ctr'); sdw=P('sd_w')+2; pb=PCT-PT; sz0=pb-P('sd_below')-0.3
sk=newsk(); rect(sk,-1,sdc-sdw/2,W+1,sdc+sdw/2); cut(N,sk,sz0,(pb+0.6)-sz0)
sk=newsk()
for x,y in EARS: circ(sk,x,y,P('m3_clear'))
cut(N,sk,-1,RH+2)
sk=newsk()
for x,y in EARS: hexa(sk,x,y,P('m3_nut_af')+0.2)
# sunk so a standard M3 x bezel_screw_l passes fully through the nut
if P('ear_nut_d') < P('m3_nut_t')+0.3 or P('ear_nut_d') > RH-3:
    raise RuntimeError('ear_nut_d %.2f out of range for bezel_screw_l' % P('ear_nut_d'))
cut(N,sk,0,P('ear_nut_d'))
# Knuckle mounting (07_knuckle turns the top joint so its bolt runs along the
# screen's long edge). Two floor ribs across the left window carry its screws.
kxb=OL/2-P('kn_fb_t')-P('yoke_off'); kyc=OW/2; ksy=P('kn_sy')
for yk in (kyc-ksy, kyc+ksy):
    sk=newsk(); rect(sk,FW-0.5,yk-4,OL/2-SPW/2+0.5,yk+4); addto(N,sk,0,FT)
if P('case_tap_d') > FT-0.8: raise RuntimeError('case_tap_d leaves <0.8mm of floor')
sk=newsk()
for kx in (kxb-P('kn_sx1'), kxb-P('kn_sx2')):
    for yk in (kyc-ksy, kyc+ksy): circ(sk,kx,yk,P('m3_tap'))
cut(N,sk,0,P('case_tap_d'))
# ---- Ventilation: vertical slots through the walls at the height of the hot gap
# between the Pi and the LCD. Vertical so each slot only bridges its own width when
# the case prints back-down. Enforced: ribs >= vent_rib, solid bands >= vent_band
# at floor and rim, >= vent_corner at corners, >= vent_rib from every opening.
# Right wall skipped (the unopened USB stack + ethernet jack sit against it); floor untouched
# (it carries the mount load through the knuckle).
vw=P('vent_w'); vrb=P('vent_rib'); vbd=P('vent_band'); vcn=P('vent_corner'); vpitch=vw+vrb
def vslots(lo,hi):
    n=int(math.floor((hi-lo+vrb)/vpitch))
    if n<=0: return []
    span=n*vw+(n-1)*vrb; s0=lo+(hi-lo-span)/2.0+vw/2.0
    return [s0+k*vpitch for k in range(n)]
def vfree(lo,hi,blocked):
    segs=[(lo,hi)]
    for b0,b1 in sorted(blocked):
        nxt=[]
        for s0,s1 in segs:
            if b1<=s0 or b0>=s1: nxt.append((s0,s1)); continue
            if b0>s0: nxt.append((s0,b0))
            if b1<s1: nxt.append((b1,s1))
        segs=nxt
    return [(a,b) for a,b in segs if b-a>=vw]
vzlo=FT+vbd; vzhi=RH-vbd
if vzhi-vzlo < 8: raise RuntimeError('vent slots would be too short')
vents={'top':0,'bottom':0,'left':0}; varea=0.0
# top wall (GPIO edge): only the corners to avoid
for a,b in vfree(vcn,OL-vcn,[]):
    for sc in vslots(a,b):
        sk=newsk(); rect(sk,sc-vw/2,OW-W-1,sc+vw/2,OW+1); cut(N,sk,vzlo,vzhi-vzlo)
        vents['top']+=1; varea+=vw*(vzhi-vzlo)
# bottom wall (power edge): clear of the USB-C hole, both flex-tab notches and the hood
ucx=PX+P('usbc_ctr'); ucw=max(P('usbc_w'),P('usbc_plug_w'))+2*FP
hd0=PX+P('adp_a')-P('adp_clr')-W; hd1=PX+P('adp_b')+P('adp_clr')+W
vblk=[(ucx-ucw/2-vrb,ucx+ucw/2+vrb),(hd0-vrb,hd1+vrb)]
for key in ('btn_pwr_ctr','btn_bkl_ctr'):
    bc_,tc_,n0_,n1_=tabgeom(key)
    vblk.append((tc_-P('tab_w')/2-P('tab_slit')-vrb, tc_+P('tab_w')/2+P('tab_slit')+vrb))
for a,b in vfree(vcn,OL-vcn,vblk):
    for sc in vslots(a,b):
        sk=newsk(); rect(sk,sc-vw/2,-1,sc+vw/2,W+1); cut(N,sk,vzlo,vzhi-vzlo)
        vents['bottom']+=1; varea+=vw*(vzhi-vzlo)
# left wall: clear of the LCD power notch and the stylus tube outside; above the SD slit
vlcy=PY+P('lcd_usbc_ctr'); vlw=P('lcd_usbc_plug_w')+2*FP
vsod=P('stylus_bore')+2*P('stylus_wall'); vsyc=P('stylus_y')
vblk=[(vlcy-vlw/2-vrb,vlcy+vlw/2+vrb),(vsyc-vsod/2-vrb,vsyc+vsod/2+vrb)]
vzlo_l=max(vzlo,(PCT-PT)+0.6+vbd)
for a,b in vfree(vcn,OW-vcn,vblk):
    for sc in vslots(a,b):
        sk=newsk(); rect(sk,-1,sc-vw/2,W+1,sc+vw/2); cut(N,sk,vzlo_l,vzhi-vzlo_l)
        vents['left']+=1; varea+=vw*(vzhi-vzlo_l)
if sum(vents.values())==0: raise RuntimeError('no vent slots placed')
place(N,0.0,0.0)
r=report(N); r['ear_nut_d']=round(P('ear_nut_d'),2)
r['vent_slots']=vents; r['vent_open_mm2']=round(varea)
return r
'''

STAGES["02"] = r'''
N='02_case_bezel'
BH=P('bezel_h'); FT2=P('bezel_face_t'); LT=P('lcd_pcb_t'); PH=P('lcd_panel_h')
sk=newsk(); rect(sk,0,0,OL,OW); mk(sk,0,BH,N)
sk=newsk()
for x,y in EARS: circ(sk,x,y,ED)
addto(N,sk,0,BH)
sk=newsk(); rect(sk,W,W,OL-W,OW-W); cut(N,sk,0,LT)
gx=PX+P('glass_off_x'); gy=PY+P('glass_off_y')
sk=newsk(); rect(sk,gx-0.3,gy-0.3,gx-0.3+P('glass_w')+0.6+P('fpc_relief'),gy-0.3+P('glass_h')+0.6)
cut(N,sk,LT,PH+P('gasket'))
# the LCD power plug overmold rises above the LCD pcb bottom face into the bezel
lw=P('lcd_usbc_plug_w')+2*FP; lcy=PY+P('lcd_usbc_ctr')
rh=P('lcd_usbc_plug_h')/2.0-P('lcd_usbc_below')+FP
if rh > 0:
    sk=newsk(); rect(sk,-1,lcy-lw/2,W+1,lcy+lw/2); cut(N,sk,-1,rh+1)
ax=PX+P('lcd_active_off_x'); ay=PY+P('lcd_active_off_y')
sk=newsk(); rect(sk,ax-0.5,ay-0.5,ax-0.5+P('lcd_active_w')+1,ay-0.5+P('lcd_active_h')+1)
cut(N,sk,LT+PH+P('gasket'),FT2+1)
sk=newsk()
for x,y in EARS: circ(sk,x,y,P('m3_clear'))
cut(N,sk,-1,BH+2)
sk=newsk()
for x,y in EARS: circ(sk,x,y,P('m3_head_d')+0.4)
cut(N,sk,BH-3.2,3.4)
# HDMI adapter hood cap: same outline as the case_back hood, full bezel height,
# relieved 0.6mm underneath so it never bears on the adapter.
if UP.itemByName('adp_hood').value > 0.5:
    ax0=PX+P('adp_a')-P('adp_clr'); ax1=PX+P('adp_b')+P('adp_clr'); yh0=PY-P('adp_out')-P('adp_clr')
    sk=newsk(); rect(sk,ax0-W,yh0-W,ax1+W,W); addto(N,sk,0,BH)
    sk=newsk(); rect(sk,ax0,yh0,ax1,W); cut(N,sk,-1,1.6)
# Flex buttons (PWR, BKL): a thin tab in the bezel's bottom wall, flush with the
# outside, hinged at the bezel face and hanging past the rim. A 45-degree wedge
# nub on its inside reaches the LCD button: press the tab, the nub pushes the
# button. ORDER MATTERS: extend the tab, cut slits + cavity, THEN add the nub
# (the cavity would otherwise cut the nub away).
tw=P('tab_w'); tt=P('tab_t'); tsl=P('tab_slit'); cav=P('tab_cav')
zr=BH-P('tab_root_skin')                     # hinge
zcb=-P('sw_below'); nh=P('nub_h'); zbt=zcb-nh/2.0-0.8
ytip=PY-P('sw_out')-P('nub_gap')             # nub tip at rest
if ytip-tt < 1.0: raise RuntimeError('nub too short: %.2f' % (ytip-tt))
MYZ=planemap(root.yZConstructionPlane)
for key in ('btn_pwr_ctr','btn_bkl_ctr'):
    bc,tc,n0,n1=tabgeom(key); bx=tc
    sk=newsk(); rect(sk,bx-tw/2,0,bx+tw/2,tt); addto(N,sk,zbt,0.5-zbt)
    sk=newsk(); rect(sk,bx-tw/2-tsl,-1,bx-tw/2,tt+cav); rect(sk,bx+tw/2,-1,bx+tw/2+tsl,tt+cav)
    cut(N,sk,zbt-1,zr-(zbt-1))
    sk=newsk(); rect(sk,bx-tw/2-tsl,tt,bx+tw/2+tsl,tt+cav); cut(N,sk,-1,zr+1)
    before=set(b.name for b in root.bRepBodies)
    zn0=zcb-nh/2.0; zn1=zcb+nh/2.0; run=ytip-tt
    sk=newyz(); L=sk.sketchCurves.sketchLines
    q=[pt(*skp(MYZ,0,y,z)) for y,z in [(tt-0.2,zn0),(ytip,zn0),(ytip,zn1),(tt-0.2,zn1+run+0.2)]]
    for k in range(4): L.addByTwoPoints(q[k],q[(k+1)%4])
    f=root.features.extrudeFeatures; i=f.createInput(profs(sk),NEW)
    i.setSymmetricExtent(vr(n1-n0),True); f.add(i)
    fresh=[b for b in root.bRepBodies if b.name not in before]
    oc=adsk.core.ObjectCollection.create()
    for b in fresh: oc.add(b)
    mf=root.features.moveFeatures; mi=mf.createInput2(oc)
    m=adsk.core.Matrix3D.create(); m.translation=adsk.core.Vector3D.create(c((n0+n1)/2.0),0,0)
    mi.defineAsFreeMove(m); mf.add(mi)
    _merge(N,[b for b in root.bRepBodies if b.name not in before])
# Stylus holder: round tube just outside the bezel's top-left, axis perpendicular
# to the screen, front flush with the screen face. The stylus drops in tip-first
# and hangs on its paddle, which can't pass the bore. The tube reaches back past
# the bezel beside (never touching) the case back, to hold the stylus straight.
sxc=P('stylus_x'); syc=P('stylus_y'); sb=P('stylus_bore')
sod=sb+2*P('stylus_wall'); sl=P('stylus_tube_l')
if sxc+sod/2.0 > -0.5: raise RuntimeError('stylus tube would touch the case back')
if syc+sod/2.0 > OW+EO-ED/2.0-1.0: raise RuntimeError('stylus tube would hit the top-left ear')
# web first: it overlaps the bezel wall, so the tube has something to fuse to
# (built the other way round, the tube floats 0.8mm clear and stays a separate body)
sk=newsk(); rect(sk,sxc,syc-3.5,0.5,syc+3.5); addto(N,sk,0,BH)
sk=newsk(); circ(sk,sxc,syc,sod); addto(N,sk,BH-sl,sl)
if sum(1 for b in root.bRepBodies if b.name.startswith(N)) != 1:
    raise RuntimeError('stylus tube did not fuse into the bezel')
sk=newsk(); circ(sk,sxc,syc,sb); cut(N,sk,BH-sl-1,sl+2)
place(N,0.0,100.0)
return report(N)
'''

STAGES["03"] = r'''
N='03_tilt_plate'
TT=P('tilt_plate_t'); TH=P('tilt_tooth_h')
sk=newsk(); rect(sk,-P('pad_w')/2,-P('pad_l')/2,P('pad_w')/2,P('pad_l')/2); mk(sk,0,TT,N)
n=teeth(N,0,0,TT,TH,PD('plate_tooth_half'))
# A printed arm's inner teeth fuse into a solid disc that levers the joint apart.
# Sink the plate's face where that disc lands.
sk=newsk(); circ(sk,0,0,2*P('tooth_relief_r'))
cut(N,sk,TT-P('tooth_relief_d'),P('tooth_relief_d')+0.01)
sk=newsk()
for dx in (-BX,BX):
    for dy in (-BY,BY): circ(sk,dx,dy,P('m3_clear'))
cut(N,sk,-1,TT+2)
sk=newsk()
for dx in (-BX,BX):
    for dy in (-BY,BY): circ(sk,dx,dy,P('m3_head_d')+0.4)
cut(N,sk,TT-2.5,3)
sk=newsk(); circ(sk,0,0,P('m3_clear')); cut(N,sk,-1,TT+TH+2)
# No nut pocket here. The nut pulls toward the arm while the arm pushes back on the
# tooth ring, so the centre is loaded in bending; with a pocket it was a 2.7mm web
# over a bridged, sagging ceiling and it caved in. The nut now sits in a pocket in
# the knuckle / base wall and bears on this plate's solid, bed-side back face.
place(N,0.0,200.0)
r=report(N); r['teeth']=n
return r
'''

STAGES["04"] = r'''
N='04_arm'
AH=P('arm_h'); AW=P('arm_w'); AT=P('arm_t'); TH=P('tilt_tooth_h')
sk=newsk(); rect(sk,0,-AW/2,AH,AW/2); mk(sk,0,AT,N)
sk=newsk(); circ(sk,0,0,AW); circ(sk,AH,0,AW); addto(N,sk,0,AT)
teeth(N,0,0,AT,TH)
teeth(N,AH,0,AT,TH)
sk=newsk(); circ(sk,0,0,P('m3_clear')); circ(sk,AH,0,P('m3_clear'))
cut(N,sk,-1,AT+TH+2)
place(N,0.0,260.0)
return report(N)
'''

STAGES["05"] = r'''
N='05_base'
BL=P('base_l'); BW=P('base_w'); BT=P('base_t')
WX=P('wall_x'); WTT=P('wall_t_top'); WTB=P('wall_t_bot'); WH=P('wall_h')
PVZ=BT+P('base_pivot_z'); pcy=P('yoke_off')-5
MXZ=planemap(root.xZConstructionPlane); MYZ=planemap(root.yZConstructionPlane)
s=root.sketches.add(root.xZConstructionPlane); circ(s,0,0,4)
f=root.features.extrudeFeatures; i=f.createInput(profs(s),NEW)
i.setDistanceExtent(False,vr(6)); b=f.add(i).bodies.item(0)
ydir=1.0 if (b.boundingBox.minPoint.y+b.boundingBox.maxPoint.y)>0 else -1.0
b.deleteMe()
sk=newsk(); rect(sk,-BL/2,pcy-BW/2,BL/2,pcy+BW/2); mk(sk,0,BT,N)
sk=newyz(); L=sk.sketchCurves.sketchLines
pts=[(0,BT-1),(0,BT+WH),(-WTT,BT+WH),(-WTB,BT-1)]
q=[pt(*skp(MYZ,0,y,z)) for y,z in pts]
for k in range(len(q)): L.addByTwoPoints(q[k],q[(k+1)%len(q)])
addtosym(N,sk,WX)
skh=newxz()
for dx in (-BY,BY):
    for dz in (-BX,BX):
        u,v=skp(MXZ,dx,0,PVZ+dz); circ(skh,u,v,P('m3_tap'))
tz=PVZ+BX; twall=WTB-(WTB-WTT)*(tz-(BT-1))/(WH+1)   # wall thickness at the upper holes
if P('base_tap_d') > twall-1.5: raise RuntimeError('base_tap_d %.1f too deep for %.1f wall' % (P('base_tap_d'),twall))
i=f.createInput(profs(skh),CUT); i.setDistanceExtent(False,vr(-P('base_tap_d')*ydir))
i.participantBodies=[bod(N)]; f.add(i)
pwall=WTB-(WTB-WTT)*(PVZ-(BT-1))/(WH+1)                 # wall thickness at the pivot
if P('tilt_tip_relief_d') > pwall-2.0: raise RuntimeError('tilt_tip_relief_d %.1f too deep for %.1f wall' % (P('tilt_tip_relief_d'),pwall))
skr=newxz(); u,v=skp(MXZ,0,0,PVZ); circ(skr,u,v,5)
i=f.createInput(profs(skr),CUT); i.setDistanceExtent(False,vr(-P('tilt_tip_relief_d')*ydir))
i.participantBodies=[bod(N)]; f.add(i)
# Tilt nut pocket behind the plate centre, one hex vertex up (the wall prints
# vertical, so the pocket roof is self-supporting instead of a flat bridge)
skn=newxz(); hexa(skn,u,v,P('m3_nut_af')+0.2,0.0 if MXZ[0]==2 else 30.0)
i=f.createInput(profs(skn),CUT); i.setDistanceExtent(False,vr(-P('tilt_nut_pocket_d')*ydir))
i.participantBodies=[bod(N)]; f.add(i)
bb=bod(N).boundingBox
hz=(bb.maxPoint.z-bb.minPoint.z)*10; hy=(bb.maxPoint.y-bb.minPoint.y)*10
if abs(hz-(BT+WH))>0.5 or abs(hy-BW)>0.5:
    raise RuntimeError('base geometry off: z=%.1f want %.1f, y=%.1f want %.1f' % (hz,BT+WH,hy,BW))
place(N,0.0,320.0)
r=report(N); r['probe']={'xz':MXZ,'yz':MYZ,'ydir':ydir}
return r
'''

STAGES["06"] = r'''
N='06_knob'
KD=P('knob_d'); KT=P('knob_t')
sk=newsk(); circ(sk,0,0,KD); mk(sk,0,KT,N)
sk=newsk()
for i in range(8):
    a=math.radians(i*45.0); circ(sk,(KD/2+1.5)*math.cos(a),(KD/2+1.5)*math.sin(a),7)
cut(N,sk,-1,KT+2)
sk=newsk(); circ(sk,0,0,P('m3_clear')); cut(N,sk,-1,KT+2)
# Round pocket on the OUTER face: the tilt bolt's socket head presses in here, so
# turning the knob turns the bolt into the nut captured behind the tilt plate. (A hex
# nut pocket here meant a round head would just spin.) The head sits deep, knob_floor_t
# above the bottom face, so an M3 x 30 still passes through that nut: a tight seat
# exactly one head tall, with a free lead-in above it.
KF=P('knob_floor_t'); HH=P('m3_head_h'); HD=P('m3_head_d')
if KF < 3.0 or KF+HH > KT: raise RuntimeError('knob_floor_t %.1f: need >= 3 and head inside a %.1f knob' % (KF,KT))
sk=newsk(); circ(sk,0,0,HD+0.1); cut(N,sk,KF,KT-KF+1)
sk=newsk(); circ(sk,0,0,HD+0.6); cut(N,sk,KF+HH,KT-KF-HH+1)
place(N,200.0,200.0)
return report(N)
'''

STAGES["reset"] = r'''
# Clear ALL geometry from the active pipeline doc in ONE timeline operation
# (per-body deleteMe took minutes). Refuses to touch any other document.
nm = app.activeDocument.name
if not (nm.startswith('wardriving-case-clean') or nm.startswith('Untitled')):
    raise RuntimeError('refusing to reset non-pipeline document: ' + nm)
tl = des.timeline
if tl.count:
    tl.moveToBeginning()
    tl.deleteAllAfterMarker()
return {'doc': nm, 'bodies': root.bRepBodies.count, 'sketches': root.sketches.count,
        'timeline': tl.count, 'params': UP.count}
'''

STAGES["save"] = r'''
d = app.activeDocument
if not d.name.startswith('wardriving-case-clean'):
    raise RuntimeError('save stage only saves the pipeline doc; active is ' + d.name)
ok = d.save('rebuilt by scripts/fusion_build.py')
return {'saved': bool(ok), 'doc': app.activeDocument.name}
'''

STAGES["fitcheck"] = r'''
# ASSEMBLY FIT CHECK, entirely in memory (TemporaryBRepManager: no timeline
# entries, nothing to clean up). Copies the case parts into their ASSEMBLED
# positions, builds proxy solids for the real hardware from the parameters,
# and reports every intersection. Then presses each button pin 1mm and checks
# that it actually reaches its switch.
tbm = adsk.fusion.TemporaryBRepManager.get()
BTI = adsk.fusion.BooleanTypes.IntersectionBooleanType
BTD = adsk.fusion.BooleanTypes.DifferenceBooleanType
def V(x,y,z): return adsk.core.Vector3D.create(c(x),c(y),c(z))
def box(x0,x1,y0,y1,z0,z1):
    ctr=adsk.core.Point3D.create(c((x0+x1)/2),c((y0+y1)/2),c((z0+z1)/2))
    obb=adsk.core.OrientedBoundingBox3D.create(ctr,V(1,0,0),V(0,1,0),c(x1-x0),c(y1-y0),c(z1-z0))
    return tbm.createBox(obb)
def cyl(x,y,z0,z1,d): return tbm.createCylinderOrCone(pt(x,y,z0),c(d/2),pt(x,y,z1),c(d/2))
def tr(b,dx,dy,dz):
    m=adsk.core.Matrix3D.create(); m.translation=V(dx,dy,dz); tbm.transform(b,m)
def rz90(b):
    m=adsk.core.Matrix3D.create()
    m.setToRotation(math.pi/2,adsk.core.Vector3D.create(0,0,1),adsk.core.Point3D.create(0,0,0))
    tbm.transform(b,m)
def inter(a,b):
    t=tbm.copy(a); tbm.booleanOperation(t,tbm.copy(b),BTI)
    if t.faces.count==0: return 0.0
    try: return round(t.volume*1000.0,2)
    except Exception: return -1.0

# ---- case parts in assembled positions ----
# build-frame min corner: the corner ears, or the adapter hood if it reaches lower
exmin=-(EO+ED/2.0); eymin=exmin
if UP.itemByName('adp_hood').value > 0.5:
    eymin=min(eymin, PY-P('adp_out')-P('adp_clr')-W)
bb=bod('01_case_back').boundingBox
CB=tbm.copy(bod('01_case_back')); tr(CB,exmin-bb.minPoint.x*10,eymin-bb.minPoint.y*10,0)
bb=bod('02_case_bezel').boundingBox
bxmin=min(exmin,P('stylus_x')-(P('stylus_bore')+2*P('stylus_wall'))/2.0)
BZ=tbm.copy(bod('02_case_bezel')); tr(BZ,bxmin-bb.minPoint.x*10,eymin-bb.minPoint.y*10,RH)
zc=RH-P('sw_below')

# ---- proxies for the real hardware (case coordinates) ----
L85=P('pi_len'); PB=PCT-PT
HX=[PX+P('pi_hole_inset'),PX+P('pi_hole_inset')+P('pi_hole_span_l')]
HY=[PY+P('pi_hole_inset'),PY+P('pi_hole_inset')+P('pi_hole_span_w')]
X={}
pi=box(PX,PX+L85,PY,PY+P('pi_wid'),PB,PCT)
for hx in HX:
    for hy in HY: tbm.booleanOperation(pi,cyl(hx,hy,PB-1,PCT+1,P('pi_hole_d')),BTD)
X['pi_pcb']=pi
uc=PX+P('usbc_ctr'); pzc=PCT+P('usbc_h')/2.0
X['pi_usbc_port']=box(uc-4.5,uc+4.5,PY-1.5,PY+7,PCT,PCT+3.3)
X['pi_power_plug']=box(uc-P('usbc_plug_w')/2,uc+P('usbc_plug_w')/2,-30,PY-2.0,
                       pzc-P('usbc_plug_h')/2,pzc+P('usbc_plug_h')/2)
for nm_,k_ in (('usb2_stack','usb2_ctr'),('usb3_stack','usb3_ctr')):
    X[nm_]=box(PX+L85-17.5,PX+L85+2.5,PY+P(k_)-7.5,PY+P(k_)+7.5,PCT,PCT+16)
X['ethernet']=box(PX+L85-21,PX+L85+3,PY+P('eth_ctr')-8,PY+P('eth_ctr')+8,PCT,PCT+13.5)
uop=P('usb2_ctr') if UP.itemByName('usb2_open').value>0.5 else P('usb3_ctr')
u3=PY+uop; upw=P('usb_plug_w')   # the plugs go in whichever stack is open
X['usb_plugs_gps_antenna']=box(PX+L85+3,PX+L85+40,u3-upw/2,u3+upw/2,PCT,PCT+16)
X['av_jack']=box(PX+P('av_ctr')-3.5,PX+P('av_ctr')+3.5,PY-2,PY+12,PCT,PCT+6)
sdc=PY+P('sd_ctr'); sz=PB-P('sd_below')+0.2
X['sd_card_inserted']=box(-30,PX+13,sdc-5.5,sdc+5.5,sz,sz+0.9)
LT=P('lcd_pcb_t'); gtop=RH+LT+P('lcd_panel_h')
X['lcd_pcb']=box(PX,PX+P('lcd_len'),PY,PY+P('lcd_wid'),RH,RH+LT)
gx=PX+P('glass_off_x'); gy=PY+P('glass_off_y')
X['glass']=box(gx,gx+P('glass_w'),gy,gy+P('glass_h'),RH+LT,gtop)
ax=PX+P('lcd_active_off_x'); ay=PY+P('lcd_active_off_y')
X['lit_area_sightline']=box(ax,ax+P('lcd_active_w'),ay,ay+P('lcd_active_h'),gtop+0.01,gtop+30)
X['hdmi_adapter']=box(PX+P('adp_a'),PX+P('adp_b'),PY-P('adp_out'),PY,RH-16.15,RH)
lyc=PY+P('lcd_usbc_ctr'); lzc=RH-P('lcd_usbc_below')
X['lcd_power_plug']=box(-30,PX-0.5,lyc-P('lcd_usbc_plug_w')/2,lyc+P('lcd_usbc_plug_w')/2,
                        lzc-P('lcd_usbc_plug_h')/2,lzc+P('lcd_usbc_plug_h')/2)
X['stylus_in_tube']=cyl(P('stylus_x'),P('stylus_y'),RH+P('bezel_h')-P('stylus_len'),RH+P('bezel_h'),P('stylus_d'))
SW={}; ACT={}
for key in ('btn_pwr_ctr','btn_bkl_ctr'):
    k3=key[4:7]
    xa=PX+P('btn_%s_a' % k3); xb=PX+P('btn_%s_b' % k3); bcx=(xa+xb)/2.0
    SW[key]=box(xa,xb,PY-P('sw_out'),PY+3,zc-1.5,RH)                    # measured body
    ACT[key]=box(bcx-0.75,bcx+0.75,PY-P('sw_out'),PY,zc-0.75,zc+0.75)   # actuator tip
    X['switch_'+k3]=SW[key]

# ---- checks ----
parts={'case_back':CB,'bezel':BZ}
hits={}
for pn,pbd in parts.items():
    for xn,xbd in X.items():
        v=inter(pbd,xbd)
        if abs(v)>0.05: hits[pn+' x '+xn]=v
v=inter(CB,BZ)
if abs(v)>0.05: hits['case_back x bezel']=v
# ---- press each flex button ----
# (a) the nub, moved by tab_press, must reach the LCD button - approximated by
#     shifting the whole bezel, which is exact at the nub (only the tabs hang
#     below the rim where the buttons are);
# (b) the tab + nub envelope, pressed, must not hit the case back.
tw=P('tab_w'); dp=P('tab_press'); zbt=RH-P('sw_below')-P('nub_h')/2.0-0.8
ytip=PY-P('sw_out')-P('nub_gap')
BZp=tbm.copy(BZ); tr(BZp,0,dp,0)
press={}
for key in ('btn_pwr_ctr','btn_bkl_ctr'):
    bc,tc,n0,n1=tabgeom(key)
    env=box(tc-tw/2,tc+tw/2,dp,ytip+dp,zbt,RH)
    press[key[4:7]]={'nub_on_actuator_mm3':inter(BZp,ACT[key]),
                     'case_back_in_the_way_mm3':inter(CB,env),
                     'tab_offset_mm':round(tc-bc,2),
                     'nub_covers_from_origin_mm':[round(n0-PX,2),round(n1-PX,2)]}
# ---- fasteners: screws aren't modelled, so check every length arithmetically ----
BH_=P('bezel_h'); TT_=P('tilt_plate_t'); nt=P('m3_nut_t'); under=TT_-2.5   # plate under the head
fz={}
L_=P('bezel_screw_l'); need=RH+BH_-3.2-P('ear_nut_d')+nt
fz['bezel M3x%g' % L_]={'past_nut_mm':round(L_-need,2),'ok':0.0<=L_-need<=P('ear_nut_d')-nt}
for lbl,L_,und,dep in (('knuckle to case',P('kn_case_screw_l'),P('kn_fa_t')-2.5,P('case_tap_d')),
                       ('tilt plate to knuckle',P('kn_plate_screw_l'),under,P('kn_fb_t')),
                       ('base plate',P('base_plate_screw_l'),under,P('base_tap_d'))):
    e=L_-und
    fz['%s M3x%g' % (lbl,L_)]={'thread_engaged_mm':round(e,2),'depth_available_mm':dep,'ok':2.5<=e<=dep-0.3}
stk=P('knob_floor_t')+P('tilt_washer_t')+P('arm_t')+P('tilt_tooth_h')+TT_   # head seat to plate back face
tip=P('tilt_bolt_l')-stk          # past the plate's back face: through the nut pocket, into the relief
npd=P('tilt_nut_pocket_d')        # the tip must clear the nut wherever it sits in its pocket
fz['tilt bolt M3x%g' % P('tilt_bolt_l')]={'tip_past_plate_mm':round(tip,2),'nut_pocket_mm':round(npd,2),
    'ok':npd+0.5<=tip<=min(P('tilt_tip_relief_d'),P('kn_fb_t'))-0.3}
ok=(not hits) and all(v['nub_on_actuator_mm3']>0 and abs(v['case_back_in_the_way_mm3'])<=0.05
                      for v in press.values()) and all(v['ok'] for v in fz.values())
return {'PASS': ok, 'collisions_mm3': hits, 'flex_button_press': press, 'fasteners': fz,
        'proxies_checked': sorted(X.keys())}
'''

STAGES["07"] = r'''
# KNUCKLE: turns the top joint 90 degrees so its bolt runs left-right, parallel
# to the screen's long edge. Both joints are then parallel: the arm swings in
# the plane the screen faces and the top joint pitches the screen (desk lamp).
# A standard 03_tilt_plate bolts to the upright; the foot bolts to the case back.
# Frame: case axes, upright's back face at x=0, joint axis at y=0, z=-kn_h
# (z<0 is behind the case back).
N='07_knuckle'
fbt=P('kn_fb_t'); fal=P('kn_fa_l'); fat=P('kn_fa_t'); hx=P('kn_h'); kw=P('kn_w')
fbh=hx+P('pad_w')/2.0+3.0
if hx < P('arm_w')/2.0+2.0: raise RuntimeError('kn_h too small: arm end would hit the case back')
sk=newsk(); rect(sk,-fal,-kw/2,fbt,kw/2); mk(sk,-fat,fat,N)          # foot
sk=newsk(); rect(sk,0,-kw/2,fbt,kw/2); addto(N,sk,-fbh,fbh)         # upright
MXZ=planemap(root.xZConstructionPlane); g=fal-2.0
gh=(g+0.5)*math.tan(math.radians(PD('kn_gusset_ang')))             # gusset rise up the upright
sk=newxz(); L=sk.sketchCurves.sketchLines
q=[pt(*skp(MXZ,x,0,z)) for x,z in [(-g,-fat+0.5),(0.5,-fat+0.5),(0.5,-fat+0.5-gh)]]
for k in range(3): L.addByTwoPoints(q[k],q[(k+1)%3])
addtosym(N,sk,kw)                                                   # gusset
# How far the driver reaches down each screw tunnel, gusset surface to screw-head top
htop=fat-2.5+P('m3_head_h')
tun={('x%g' % sx):round(max(0.0,gh*(g-sx)/(g+0.5)-0.5-(htop-fat)),1) for sx in (P('kn_sx1'),P('kn_sx2'))}
MYZ=planemap(root.yZConstructionPlane)
sk=newyz()
for dy in (-BY,BY):
    for dz in (-BX,BX):
        u,v=skp(MYZ,0,dy,-hx+dz); circ(sk,u,v,P('m3_tap'))            # tilt-plate screws
u,v=skp(MYZ,0,0,-hx); circ(sk,u,v,5)                                 # bolt-tip relief
f=root.features.extrudeFeatures; i=f.createInput(profs(sk),CUT)
i.setSymmetricExtent(vr(2*fbt),True); i.participantBodies=[bod(N)]; f.add(i)
# Tilt nut pocket in the plate face (x=fbt), one vertex along z so its roof is
# self-supporting when the knuckle prints foot-down. The YZ sketch plane sits at
# x=0, so cut with a symmetric tool body moved out to the face.
pd=P('tilt_nut_pocket_d')
if pd > fbt-2.0: raise RuntimeError('nut pocket %.1f leaves < 2mm of the %.1f upright' % (pd,fbt))
before=set(b.name for b in root.bRepBodies)
sk=newyz(); u,v=skp(MYZ,0,0,-hx); hexa(sk,u,v,P('m3_nut_af')+0.2,0.0 if MYZ[0]==2 else 30.0)
i=f.createInput(profs(sk),NEW); i.setSymmetricExtent(vr(pd+1.0),True); f.add(i)
oc=adsk.core.ObjectCollection.create()
for b in root.bRepBodies:
    if b.name not in before: oc.add(b)
mf=root.features.moveFeatures; mi=mf.createInput2(oc)
m=adsk.core.Matrix3D.create(); m.translation=adsk.core.Vector3D.create(c(fbt+(1.0-pd)/2.0),0,0)
mi.defineAsFreeMove(m); mf.add(mi)
_merge(N,[b for b in root.bRepBodies if b.name not in before],FO.CutFeatureOperation)
sk=newsk()
for sx in (P('kn_sx1'),P('kn_sx2')):
    for sy in (-P('kn_sy'),P('kn_sy')): circ(sk,-sx,sy,P('m3_head_d')+1.0)
cut(N,sk,-fbh-2,fbh+2-(fat-2.5))                                    # counterbore + driver tunnel
sk=newsk()
for sx in (P('kn_sx1'),P('kn_sx2')):
    for sy in (-P('kn_sy'),P('kn_sy')): circ(sk,-sx,sy,P('m3_clear'))
cut(N,sk,-fat-0.5,fat+1.5)
place(N,60.0,200.0)
r=report(N); r['axis_behind_case_back_mm']=round(hx,2); r['head_depth_in_tunnel_mm']=tun
return r
'''

STAGES["mountcheck"] = r'''
# MOUNT KINEMATICS CHECK, in memory. Sweeps both tilt joints in 10-degree steps
# and checks the arm against the base, knuckle, case back and bezel at each step.
# Also checks the configuration the user needs: arm leaning by L degrees with the
# screen upright and facing the lean direction, which requires top angle = L.
tbm=adsk.fusion.TemporaryBRepManager.get()
BTI=adsk.fusion.BooleanTypes.IntersectionBooleanType
def Vd(x,y,z): return adsk.core.Vector3D.create(x,y,z)
def Pc(q): return adsk.core.Point3D.create(c(q[0]),c(q[1]),c(q[2]))
def xf(b,m): tbm.transform(b,m); return b
def shift(dx,dy,dz):
    m=adsk.core.Matrix3D.create(); m.translation=adsk.core.Vector3D.create(c(dx),c(dy),c(dz)); return m
def frame(o,xa,ya,za):
    m=adsk.core.Matrix3D.create(); m.setWithCoordinateSystem(Pc(o),Vd(*xa),Vd(*ya),Vd(*za)); return m
def rot(deg,axis,o):
    m=adsk.core.Matrix3D.create(); m.setToRotation(math.radians(deg),Vd(*axis),Pc(o)); return m
def box(x0,x1,y0,y1,z0,z1):
    ctr=adsk.core.Point3D.create(c((x0+x1)/2),c((y0+y1)/2),c((z0+z1)/2))
    obb=adsk.core.OrientedBoundingBox3D.create(ctr,Vd(1,0,0),Vd(0,1,0),c(x1-x0),c(y1-y0),c(z1-z0))
    return tbm.createBox(obb)
def inter(a,b):
    q=tbm.copy(a); tbm.booleanOperation(q,tbm.copy(b),BTI)
    if q.faces.count==0: return 0.0
    try: return round(q.volume*1000.0,2)
    except Exception: return -1.0
def unplaced(nm,bx0,by0):
    b=bod(nm); bb=b.boundingBox
    return xf(tbm.copy(b),shift(bx0-bb.minPoint.x*10,by0-bb.minPoint.y*10,0))
def sweep(parts,place_arm,angles):
    blocked={}; free=[]
    for a in angles:
        A=place_arm(tbm.copy(ARM0),a)
        hit={n:inter(A,b) for n,b in parts.items()}
        hit={n:v for n,v in hit.items() if abs(v)>0.05}
        if hit: blocked[str(a)]=hit
        else: free.append(a)
    return free,blocked
AW=P('arm_w'); AT=P('arm_t'); AH=P('arm_h'); TT=P('tilt_plate_t'); TH=P('tilt_tooth_h')
ARM0=unplaced('04_arm',-AW/2.0,-AW/2.0)

# ---- base joint: axis along base y through (0,0,PVZ); arm leans toward +x ----
BL=P('base_l'); BW=P('base_w'); PVZ=P('base_t')+P('base_pivot_z'); pcy=P('yoke_off')-5
BASE=unplaced('05_base',-BL/2.0,pcy-BW/2.0)
bpad=box(-P('pad_l')/2,P('pad_l')/2,0,TT,PVZ-P('pad_w')/2,PVZ+P('pad_w')/2)
def arm_on_base(A,th):
    xf(A,frame((0,TT+TH+AT,PVZ),(0,0,1),(-1,0,0),(0,-1,0)))
    return xf(A,rot(th,(0,1,0),(0,0,PVZ)))
bfree,bblk=sweep({'base':BASE,'base_tilt_plate':bpad},arm_on_base,list(range(-90,91,10)))
hub_clear=round(PVZ-AW/2.0-P('base_t'),2)

# ---- top joint: axis along case x (screen's long edge) ----
exmin=-(EO+ED/2.0); eymin=exmin
if UP.itemByName('adp_hood').value > 0.5: eymin=min(eymin,PY-P('adp_out')-P('adp_clr')-W)
CB=unplaced('01_case_back',exmin,eymin)
bxmin=min(exmin,P('stylus_x')-(P('stylus_bore')+2*P('stylus_wall'))/2.0)
BZ=xf(unplaced('02_case_bezel',bxmin,eymin),shift(0,0,RH))
STY=tbm.createCylinderOrCone(pt(P('stylus_x'),P('stylus_y'),RH+P('bezel_h')-P('stylus_len')),c(P('stylus_d')/2),pt(P('stylus_x'),P('stylus_y'),RH+P('bezel_h')),c(P('stylus_d')/2))
fbt=P('kn_fb_t'); hx=P('kn_h'); kxb=OL/2-fbt-P('yoke_off'); kyc=OW/2
KN=xf(unplaced('07_knuckle',-P('kn_fa_l'),-P('kn_w')/2.0),shift(kxb,kyc,0))
tpad=box(kxb+fbt,kxb+fbt+TT,kyc-P('pad_l')/2,kyc+P('pad_l')/2,-hx-P('pad_w')/2,-hx+P('pad_w')/2)
pv=(kxb+fbt+TT+TH+AT,kyc,-hx)
def arm_on_case(A,ps):
    cs=math.cos(math.radians(ps)); sn=math.sin(math.radians(ps))
    d=(0,cs,sn)                                   # arm 'up' in case coords
    o=(pv[0]-AH*d[0],pv[1]-AH*d[1],pv[2]-AH*d[2])
    return xf(A,frame(o,d,(0,sn,-cs),(-1,0,0)))
tfree,tblk=sweep({'case_back':CB,'bezel':BZ,'knuckle':KN,'top_tilt_plate':tpad,'stylus':STY},arm_on_case,list(range(-30,211,10)))

# ---- the user's configuration: lean L, screen upright facing the lean => top angle L ----
ok_leans=[L for L in range(0,91,10) if L in bfree and L in tfree]
ok=(ok_leans==list(range(0,91,10))) and hub_clear>=2.0
return {'PASS':ok,
        'screen_upright_facing_lean_ok_for_leans_deg':ok_leans,
        'base_joint':{'free_deg':bfree,'blocked':bblk,'arm_end_above_plate_mm':hub_clear},
        'top_joint':{'free_deg':tfree,'blocked':tblk},
        'axes':'both joint axes horizontal and parallel; top axis = screen long edge'}
'''

STAGES["toothcheck"] = r'''
# Does the tooth ring actually mesh IN THE MODEL? Mates a tilt plate face-to-face
# with the arm's top hub at several rotations and measures the overlap. Zero at
# some angle means the CAD is fine and any fit problem is printability.
tbm=adsk.fusion.TemporaryBRepManager.get()
BTI=adsk.fusion.BooleanTypes.IntersectionBooleanType
def Vd(x,y,z): return adsk.core.Vector3D.create(x,y,z)
def xf(b,m): tbm.transform(b,m); return b
def shift(dx,dy,dz):
    m=adsk.core.Matrix3D.create(); m.translation=adsk.core.Vector3D.create(c(dx),c(dy),c(dz)); return m
def rot(deg,axis,o):
    m=adsk.core.Matrix3D.create(); m.setToRotation(math.radians(deg),Vd(*axis),pt(*o)); return m
def inter(a,b):
    q=tbm.copy(a); tbm.booleanOperation(q,tbm.copy(b),BTI)
    if q.faces.count==0: return 0.0
    try: return round(q.volume*1000.0,2)
    except Exception: return -1.0
def unplaced(nm,bx0,by0):
    b=bod(nm); bb=b.boundingBox
    return xf(tbm.copy(b),shift(bx0-bb.minPoint.x*10,by0-bb.minPoint.y*10,0))
AW=P('arm_w'); AH=P('arm_h'); AT=P('arm_t'); TT=P('tilt_plate_t'); TH=P('tilt_tooth_h')
ARM=unplaced('04_arm',-AW/2.0,-AW/2.0)
PL0=unplaced('03_tilt_plate',-P('pad_w')/2.0,-P('pad_l')/2.0)
res={}
for phi in (0.0,2.5,5.0,7.5,10.0):
    PL=tbm.copy(PL0)
    xf(PL,rot(180.0,(1,0,0),(0,0,0)))          # face the teeth at each other
    xf(PL,shift(AH,0.0,AT+TT+TH))              # plate tips land on the arm's tooth roots
    xf(PL,rot(phi,(0,0,1),(AH,0,0)))           # rotate about the joint axis
    res['%.1f' % phi]=inter(ARM,PL)
mesh=[k for k,v in res.items() if abs(v)<=0.5]
return {'overlap_mm3_by_rotation':res,'meshes_at_deg':mesh,
        'reading':'0 overlap = teeth interleave; large overlap = tooth tips colliding'}
'''

STAGES["info"] = r'''
names=[b.name for b in root.bRepBodies]
return {'doc':app.activeDocument.name,'open_docs':[d.name for d in app.documents],
        'bodies':len(names),'sketches':root.sketches.count,'timeline':des.timeline.count,
        'params':UP.count,'first':names[:8]}
'''

STAGES["verify"] = r'''
want=['01_case_back','02_case_bezel','03_tilt_plate','04_arm','05_base','06_knob','07_knuckle']
names=[b.name for b in root.bRepBodies]
boxes=[]
for b in root.bRepBodies:
    bb=b.boundingBox
    boxes.append((b.name,[bb.minPoint.x*10,bb.minPoint.y*10,bb.minPoint.z*10],
                        [bb.maxPoint.x*10,bb.maxPoint.y*10,bb.maxPoint.z*10]))
ov=[]
for i in range(len(boxes)):
    for j in range(i+1,len(boxes)):
        a,b2=boxes[i],boxes[j]
        if all(a[1][k]<b2[2][k]-1e-6 and b2[1][k]<a[2][k]-1e-6 for k in range(3)):
            ov.append((a[0],b2[0]))
gaps=[]
for i in range(len(boxes)):
    for j in range(i+1,len(boxes)):
        a,b2=boxes[i],boxes[j]
        dx=max(0,max(a[1][0]-b2[2][0], b2[1][0]-a[2][0]))
        dy=max(0,max(a[1][1]-b2[2][1], b2[1][1]-a[2][1]))
        gaps.append(round(max(dx,dy),1))
return {'bodies':root.bRepBodies.count,'names':names,
        'missing':[w for w in want if w not in names],
        'extra':[n for n in names if n not in want],
        'OVERLAPS':ov,'min_gap_mm':min(gaps) if gaps else None,
        'all_valid':all(b.isValid for b in root.bRepBodies),
        'pla_g_100pct':round(sum(b.volume for b in root.bRepBodies)*1.24,1)}
'''

ORDER = ["01", "02", "03", "04", "05", "06", "07", "verify"]


def params_stage():
    rows = ",\n".join(
        "        (%r, %r, %r, %r)" % p for p in PARAMS)
    return """
SPEC = [
%s,
]
made, failed = [], []
for nm, ex, un, cm in SPEC:
    try:
        p = UP.itemByName(nm)
        if p: p.expression = ex
        else: UP.add(nm, adsk.core.ValueInput.createByString(ex), un, cm)
        made.append(nm)
    except Exception as e:
        failed.append((nm, ex, str(e)))
keep = set(s[0] for s in SPEC); pruned = []
for p_ in list(UP):
    if p_.name not in keep:
        try:
            nm_ = p_.name; p_.deleteMe(); pruned.append(nm_)
        except Exception as e:
            failed.append((p_.name, 'prune', str(e)))
return {'created': len(made), 'pruned': pruned, 'failed': failed, 'total': UP.count}
""" % rows


def build_code(stage):
    if stage == "params":
        src = HELPERS + params_stage()
    elif stage in ("newdoc", "closescratch", "reset", "save", "verify", "info"):
        src = HELPERS + STAGES[stage]
    else:
        src = HELPERS + SHARED + STAGES[stage]
    indented = "\n".join(("    " + l) if l.strip() else l for l in src.splitlines())
    return ("def build():\n" + indented +
            "\n\ntry:\n    result = {'output': build()}\n"
            "except Exception as e:\n"
            "    import traceback\n"
            "    result = {'output': {'ERROR': str(e), 'tb': traceback.format_exc()[-700:]}}\n")


if __name__ == "__main__":
    a = sys.argv[1:]
    if not a or a[0] in ("-h", "--help"):
        print(__doc__); sys.exit(0)
    if a[0] == "--list":
        print("params"); [print(s) for s in ORDER]; sys.exit(0)
    if a[0] == "--all":
        for s in ["params"] + ORDER:
            print("# ===== STAGE %s =====" % s); print(build_code(s)); print()
        sys.exit(0)
    print(build_code(a[0]))
