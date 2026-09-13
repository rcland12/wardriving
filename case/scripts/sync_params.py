#!/usr/bin/env python3
"""Read MEASUREMENTS.yaml -> emit Fusion parameter updates.

Usage:
    python scripts/sync_params.py            # show what would change
    python scripts/sync_params.py --emit     # print the Fusion python to run

Only entries whose `src:` is `measured` or `choice` are pushed. Anything
still marked `datasheet` or `MEASURE` is reported as outstanding so we
never silently build on a guess.
"""
import sys, json, pathlib

try:
    import yaml
except ImportError:
    sys.exit("need pyyaml:  source ~/.global_venv/bin/activate && pip install pyyaml")

ROOT = pathlib.Path(__file__).resolve().parent.parent

# YAML dotted path  ->  Fusion user-parameter name
MAP = {
    "pi_board.length": "pi_len",
    "pi_board.width": "pi_wid",
    "pi_board.pcb_thickness": "pi_pcb_t",
    "pi_board.hole_dia": "pi_hole_d",
    "pi_board.hole_inset_long": "pi_hole_inset",
    "pi_board.hole_span_long": "pi_hole_span_l",
    "pi_board.hole_span_short": "pi_hole_span_w",
    "pi_board.top_component_h": "pi_top_comp_h",
    "pi_board.under_component_h": "pi_under_comp_h",

    "pi_bottom_edge.usbc.ctr": "usbc_ctr",
    "pi_bottom_edge.usbc.open_w": "usbc_w",
    "pi_bottom_edge.usbc.open_h": "usbc_h",
    "pi_bottom_edge.hdmi0.ctr": "hdmi0_ctr",
    "pi_bottom_edge.hdmi1.ctr": "hdmi1_ctr",
    "pi_bottom_edge.hdmi0.open_w": "hdmi_w",
    "pi_bottom_edge.hdmi0.open_h": "hdmi_h",
    "pi_bottom_edge.av_jack.ctr": "av_ctr",
    "pi_bottom_edge.av_jack.dia": "av_d",

    "pi_usb_edge.ethernet.ctr": "eth_ctr",
    "pi_usb_edge.ethernet.open_w": "eth_w",
    "pi_usb_edge.ethernet.open_h": "eth_h",
    "pi_usb_edge.usb3_pair.ctr": "usb3_ctr",
    "pi_usb_edge.usb2_pair.ctr": "usb2_ctr",
    "pi_usb_edge.usb3_pair.open_w": "usb_w",
    "pi_usb_edge.usb3_pair.open_h": "usb_h",

    "pi_sd.ctr": "sd_ctr",
    "pi_sd.slot_w": "sd_w",
    "pi_sd.below_pcb": "sd_below",

    "lcd.pcb_length": "lcd_len",
    "lcd.pcb_width": "lcd_wid",
    "lcd.pcb_thickness": "lcd_pcb_t",
    "lcd.active_w": "lcd_active_w",
    "lcd.active_h": "lcd_active_h",
    "lcd.active_off_x": "lcd_active_off_x",
    "lcd.active_off_y": "lcd_active_off_y",
    "lcd.panel_h": "lcd_panel_h",
    "lcd.bezel_grip_avail": "bezel_grip",

    "stack.gap_pi_to_lcd": "stack_gap",

    "car.arm_height": "arm_h",
    "car.base_l": "base_l",
    "car.base_w": "base_w",

    "fit.port_clearance": "fit_port",
    "fit.board_clearance": "fit_board",
    "fit.wall_thickness": "wall",
    "fit.floor_thickness": "floor_t",
    "fit.hole_shrink_comp": "hole_comp",
}

PUSHABLE = {"measured", "choice"}


def dig(doc, dotted):
    node = doc
    for key in dotted.split("."):
        if not isinstance(node, dict) or key not in node:
            return None
        node = node[key]
    return node


def main():
    doc = yaml.safe_load((ROOT / "MEASUREMENTS.yaml").read_text())
    updates, pending = {}, []

    for path, param in MAP.items():
        entry = dig(doc, path)
        if entry is None:
            pending.append((path, param, "missing from sheet"))
            continue
        val, src = entry.get("value"), entry.get("src")
        if val is None:
            pending.append((path, param, "value is null"))
        elif src in PUSHABLE:
            updates[param] = float(val)
        else:
            pending.append((path, param, f"src={src}, needs verifying"))

    if "--emit" in sys.argv:
        lines = [
            "import adsk.core",
            "app = adsk.core.Application.get()",
            "ups = app.activeProduct.userParameters",
            f"VALS = {json.dumps(updates, indent=1)}",
            "done, miss = [], []",
            "for n, v in VALS.items():",
            "    p = ups.itemByName(n)",
            "    if p:",
            "        p.expression = '%g mm' % v",
            "        done.append(n)",
            "    else:",
            "        miss.append(n)",
            "result = {'output': {'updated': done, 'not_found': miss}}",
        ]
        print("\n".join(lines))
        return

    print(f"READY TO PUSH  ({len(updates)})")
    for k, v in sorted(updates.items()):
        print(f"   {k:<20} = {v} mm")
    print(f"\nOUTSTANDING  ({len(pending)})")
    for path, param, why in pending:
        print(f"   {path:<38} -> {param:<20} {why}")


if __name__ == "__main__":
    main()
