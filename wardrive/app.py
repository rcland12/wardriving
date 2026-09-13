"""Main loop: frame (status bar, nav column, footer), views, modals and input."""

from __future__ import annotations

import logging
import time
from datetime import datetime
from pathlib import Path

import pygame

from . import fmt, theme
from .config import Config
from .display import Display
from .state import Capture, State
from .touch import Calibration, EvdevTouch, TouchEvent
from .views import FOOTER, NAV, STATUS, Modal, View
from .views.calibrate import CalibrateView
from .views.gps import GpsView, fix_label
from .views.log import LogView
from .views.menu import MenuView
from .views.nets import NetsView
from .views.stats import StatsView
from .views.upload import UploadView
from .widgets import Button

log = logging.getLogger(__name__)


class SummaryModal(Modal):
    title = "Session saved"

    def lines(self):
        s = self.app.state.summary
        files = ", ".join(f for f in s.files if f.endswith(".wiglecsv")) or "(none)"
        return [
            ("Duration", fmt.duration(s.duration), theme.TEXT),
            ("Wi-Fi APs", fmt.count(s.wifi), theme.TEXT),
            ("Open", fmt.count(s.open), theme.RED),
            ("Bluetooth", fmt.count(s.bt), theme.PURPLE),
            ("WiGLE log", files, theme.DIM),
            ("", "Upload later from MENU → UPLOAD", theme.DIM),
        ]


class App:
    def __init__(self, cfg: Config, state: State, backend, display: Display, touch: EvdevTouch | None):
        self.cfg, self.state, self.backend, self.display, self.touch = cfg, state, backend, display, touch
        self.calibration: Calibration | None = Calibration.load(cfg.calibration_path)
        self.running = True
        self.modal: Modal | None = None
        self.shutting_down = 0.0
        self.snapshot_requested = False  # set by SIGUSR1: dump the canvas to /tmp
        self._summary_shown = None

        self.views: dict[str, View] = {
            v.name: v
            for v in (
                NetsView(self), StatsView(self), GpsView(self), LogView(self),
                MenuView(self), UploadView(self), CalibrateView(self),
            )
        }
        self.current: View = self.views["nets"]
        self.nav = self._build_nav()
        if touch is not None and self.calibration is None:
            self.show("calibrate")

    # --- navigation ------------------------------------------------------------

    def show(self, name: str) -> None:
        self.current = self.views[name]
        self.current.on_show()

    def open_modal(self, modal: Modal) -> None:
        self.modal = modal

    def close_modal(self) -> None:
        self.modal = None

    def touch_connected(self) -> bool:
        return self.touch is None or self.touch.connected

    def _build_nav(self) -> list[Button]:
        st = self.state
        x, y, w = NAV.x, NAV.y, NAV.w
        half = (w - 4) // 2

        def capture_label():
            return {
                Capture.IDLE: "START",
                Capture.WAITING: "CANCEL",
                Capture.STARTING: "STARTING",
                Capture.RUNNING: "STOP",
                Capture.STOPPING: "STOPPING",
                Capture.ERROR: "RETRY",
            }[st.capture]

        def capture_sublabel():
            return "for GPS time" if st.capture == Capture.WAITING else ""

        def capture_color():
            if st.capture == Capture.RUNNING:
                return theme.DARK_RED
            if st.capture in (Capture.IDLE, Capture.ERROR):
                return theme.DARK_GREEN
            return theme.PANEL

        def capture_tap():
            if st.capture in (Capture.RUNNING, Capture.WAITING):
                self.backend.stop_capture()
            elif st.capture in (Capture.IDLE, Capture.ERROR):
                self.backend.start_capture()

        def nav(name):
            return lambda: self.current.name == name or (name == "menu" and self.current.name == "upload")

        return [
            Button((x, y, w, 76), capture_label, capture_tap, size=20, color=capture_color,
                   sublabel=capture_sublabel,
                   enabled=lambda: st.capture not in (Capture.STARTING, Capture.STOPPING)),
            Button((x, y + 80, half, 56), "NETS", lambda: self.show("nets"), size=13, active=nav("nets")),
            Button((x + half + 4, y + 80, half, 56), "STATS", lambda: self.show("stats"), size=13, active=nav("stats")),
            Button((x, y + 140, half, 56), "GPS", lambda: self.show("gps"), size=13, active=nav("gps")),
            Button((x + half + 4, y + 140, half, 56), "LOG", lambda: self.show("log"), size=13, active=nav("log")),
            Button((x, y + 200, w, 58), "MENU", lambda: self.show("menu"), size=15, active=nav("menu")),
        ]

    # --- input -----------------------------------------------------------------

    def _collect_events(self) -> list[TouchEvent]:
        events: list[TouchEvent] = []
        for e in pygame.event.get():
            if e.type == pygame.QUIT:
                self.running = False
            elif e.type == pygame.KEYDOWN and e.key in (pygame.K_ESCAPE, pygame.K_q) and self.display.mode == "window":
                self.running = False
            elif self.touch is None and e.type in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP, pygame.MOUSEMOTION):
                if e.type == pygame.MOUSEMOTION and not e.buttons[0]:
                    continue
                if e.type != pygame.MOUSEMOTION and e.button != 1:
                    continue
                kind = {pygame.MOUSEBUTTONDOWN: "down", pygame.MOUSEBUTTONUP: "up", pygame.MOUSEMOTION: "move"}[e.type]
                pos = self.display.window_to_canvas(e.pos)
                events.append(TouchEvent(kind, pos, pos))
        if self.touch is not None:
            cal = self.calibration or Calibration.identity()
            while not self.touch.events.empty():
                kind, rx, ry = self.touch.events.get_nowait()
                events.append(TouchEvent(kind, cal.map(rx, ry), (rx, ry)))
        return events

    def _dispatch(self, ev: TouchEvent) -> None:
        if self.shutting_down:
            return
        if self.modal:
            self.modal.handle(ev)
            return
        if not self.current.fullscreen and any(b.handle(ev) for b in self.nav):
            return
        self.current.handle(ev)

    # --- drawing ---------------------------------------------------------------

    def _draw_status(self, surf: pygame.Surface, now: float) -> None:
        st = self.state
        pygame.draw.rect(surf, theme.PANEL, STATUS)
        cy = STATUS.centery
        if st.capture == Capture.RUNNING:
            if int(now * 2) % 2 == 0:
                pygame.draw.circle(surf, theme.RED, (12, cy), 6)
            theme.blit_text(surf, "REC " + fmt.duration(st.session_seconds()), (24, cy), 14, theme.TEXT, bold=True, anchor="midleft")
        else:
            label, color = {
                Capture.IDLE: ("IDLE", theme.DIM),
                Capture.WAITING: ("GPS TIME…", theme.AMBER),
                Capture.STARTING: ("STARTING…", theme.AMBER),
                Capture.STOPPING: ("STOPPING…", theme.AMBER),
                Capture.ERROR: ("ERROR", theme.RED),
            }.get(st.capture, ("", theme.DIM))
            pygame.draw.circle(surf, color, (12, cy), 6, 0 if st.capture != Capture.IDLE else 1)
            theme.blit_text(surf, label, (24, cy), 14, color, bold=True, anchor="midleft")

        g = st.gps
        label, color = fix_label(g)
        gps_text = f"GPS {label.replace(' FIX', '') if g.mode >= 2 else label}"
        if g.device_present:
            gps_text += f" · {g.sats_used} sat"
        theme.blit_text(surf, gps_text, (250, cy), 13, color, bold=True, anchor="center")

        right = STATUS.right - 6
        # An unsynced clock is hours or days off after a boot away from Wi-Fi; don't show it as fact.
        if st.sys.clock_source == "unsynced":
            clock_text, clock_color = "--:--", theme.AMBER
        else:
            clock_text, clock_color = datetime.now().strftime("%H:%M"), theme.TEXT
        clock = theme.blit_text(surf, clock_text, (right, cy), 14, clock_color, bold=True, anchor="midright")
        right = clock.left - 10
        s = st.sys
        if s.undervolt_now or s.throttled_now:
            r = theme.blit_text(surf, "PWR!", (right, cy), 13, theme.RED, bold=True, anchor="midright")
            right = r.left - 8
        if s.cpu_temp:
            color = theme.RED if s.cpu_temp >= 80 else theme.AMBER if s.cpu_temp >= 70 else theme.DIM
            theme.blit_text(surf, f"{s.cpu_temp:.0f}°", (right, cy), 13, color, anchor="midright")

    def _draw_footer(self, surf: pygame.Surface) -> None:
        st = self.state
        pygame.draw.rect(surf, theme.PANEL, FOOTER)
        cy = FOOTER.centery
        color = theme.DIM
        if st.upload_busy:
            text, color = st.upload_status, theme.AMBER
        elif st.capture == Capture.ERROR:
            text, color = st.capture_error or "Capture error", theme.RED
        elif st.capture == Capture.WAITING:
            text, color = "Clock not set. Capture starts when GPS time arrives (needs a fix).", theme.AMBER
        elif st.capture in (Capture.RUNNING, Capture.STOPPING):
            c = st.counts()
            text = f"Wi-Fi {c['wifi']:,} · BT {c['bt']:,} · open {c['OPEN']:,} · {st.packets_per_sec:.0f} pkt/s"
            if st.distance_m:
                text += " · " + fmt.distance(st.distance_m, self.cfg.units)
            color = theme.TEXT
        elif st.summary:
            s = st.summary
            text = f"Last session {fmt.duration(s.duration)} · {s.wifi:,} Wi-Fi · {s.bt:,} BT"
        elif st.sys.clock_source == "unsynced":
            text = "Clock not set yet. START will wait for GPS time."
        elif not st.gps.has_fix:
            text = "No GPS fix: networks found now won't have locations."
        else:
            text = "Ready. Tap START to begin."
        free = st.sys.disk_free_bytes
        if free and free < 1 << 30:
            text, color = f"LOW DISK: {fmt.size(free)} free", theme.RED
        theme.blit_text(surf, theme.fit(text, 13, FOOTER.w - 12), (8, cy), 13, color, anchor="midleft")

    def draw(self, now: float) -> None:
        surf = self.display.canvas
        surf.fill(theme.BG)
        if self.shutting_down:
            theme.blit_text(surf, "Shutting down…", (240, 140), 24, theme.AMBER, bold=True, anchor="center")
            theme.blit_text(surf, "Wait for the green LED to stop blinking", (240, 175), 14, theme.DIM, anchor="center")
            return
        if self.current.fullscreen:
            self.current.draw(surf, now)
        else:
            self._draw_status(surf, now)
            self.current.draw(surf, now)
            for b in self.nav:
                b.draw(surf, now)
            self._draw_footer(surf)
        if self.modal:
            self.modal.draw(surf, now)

    # --- loop ------------------------------------------------------------------

    def tick(self) -> None:
        now = time.monotonic()
        for ev in self._collect_events():
            self._dispatch(ev)
        for b in self.nav:
            b.update(now)
        self.current.update(now)
        # Pop the session summary once, right after a capture stops.
        if self.state.summary is not None and self.state.summary is not self._summary_shown:
            self._summary_shown = self.state.summary
            if not self.current.fullscreen:
                self.open_modal(SummaryModal(self))
        self.draw(now)
        self.display.present()

    def run(self) -> None:
        clock = pygame.time.Clock()
        while self.running:
            self.tick()
            if self.snapshot_requested:
                self.snapshot_requested = False
                path = Path(f"/tmp/wardrive-screen-{self.current.name}.png")
                pygame.image.save(self.display.canvas, str(path))
                log.info("saved screen to %s", path)
            clock.tick(self.cfg.display.fps)

    def screenshots(self, outdir: Path) -> list[Path]:
        """Render every screen to PNG (used with the mock backend, headless)."""
        outdir.mkdir(parents=True, exist_ok=True)
        paths = []

        def snap(name: str):
            self.draw(time.monotonic())
            path = outdir / f"{name}.png"
            pygame.image.save(self.display.canvas, str(path))
            paths.append(path)

        for name in ("nets", "stats", "gps", "log", "menu", "upload", "calibrate"):
            self.show(name)
            snap(name)
        self.show("nets")
        rows = self.views["nets"].rows()
        if rows:
            from .views.nets import DeviceModal

            self.open_modal(DeviceModal(self, rows[0]))
            snap("device-detail")
            self.close_modal()
        return paths
