# Shared preamble injected into every Fusion build push.
# Fusion's API works in CENTIMETRES; everything here is authored in mm
# and converted at the boundary by c().
PREAMBLE = r'''
import adsk.core, adsk.fusion, math
app = adsk.core.Application.get()
des = adsk.fusion.Design.cast(app.activeProduct)
root = des.rootComponent
UP = des.userParameters
FO = adsk.fusion.FeatureOperations
NEW, CUT, JOIN = FO.NewBodyFeatureOperation, FO.CutFeatureOperation, FO.JoinFeatureOperation

def P(name):
    "user parameter value, in mm"
    return UP.itemByName(name).value * 10.0

def c(mm):
    return mm / 10.0

def vr(mm):
    return adsk.core.ValueInput.createByReal(c(mm))

def pt(x, y, z=0):
    return adsk.core.Point3D.create(c(x), c(y), c(z))

def newsk():
    return root.sketches.add(root.xYConstructionPlane)

def rect(sk, x0, y0, x1, y1):
    sk.sketchCurves.sketchLines.addTwoPointRectangle(pt(x0, y0), pt(x1, y1))

def circ(sk, cx, cy, d):
    sk.sketchCurves.sketchCircles.addByCenterRadius(pt(cx, cy), c(d / 2.0))

def hexa(sk, cx, cy, af):
    "hex pocket by across-flats"
    r = af / math.sqrt(3.0)
    lines = sk.sketchCurves.sketchLines
    ps = [pt(cx + r * math.cos(math.radians(60 * i + 30)),
             cy + r * math.sin(math.radians(60 * i + 30))) for i in range(6)]
    for i in range(6):
        lines.addByTwoPoints(ps[i], ps[(i + 1) % 6])

def profs(sk):
    oc = adsk.core.ObjectCollection.create()
    for p in sk.profiles:
        oc.add(p)
    return oc

def ext(sk_or_prof, z0, h, op, target=None, name=None):
    src = sk_or_prof if not isinstance(sk_or_prof, adsk.fusion.Sketch) else profs(sk_or_prof)
    f = root.features.extrudeFeatures
    inp = f.createInput(src, op)
    if abs(z0) > 1e-9:
        inp.startExtent = adsk.fusion.OffsetStartDefinition.create(vr(z0))
    inp.setDistanceExtent(False, vr(h))
    if target is not None:
        inp.participantBodies = [target]
    r = f.add(inp)
    if name and r.bodies.count:
        r.bodies.item(0).name = name
    return r

def wipe():
    "full rebuild: drop every body and sketch"
    for b in list(root.bRepBodies):
        b.deleteMe()
    for s in list(root.sketches):
        s.deleteMe()
    for p in list(root.constructionPlanes):
        p.deleteMe()

def body(nm):
    b = root.bRepBodies.itemByName(nm)
    if b is None:
        raise RuntimeError('no body named ' + nm)
    return b
'''
