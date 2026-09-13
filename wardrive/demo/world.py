"""Simulated drives: a car that follows real roads, and the networks it hears on the way.

Nothing here touches hardware. Roads, buildings and businesses come from the offline map's
street-level tiles. Networks are generated from a fixed seed for each ~60 m cell, so driving
down the same street twice hears the same routers, like a real neighbourhood. Without a map
(or away from its roads) the car drives a plain street grid instead.
"""

from __future__ import annotations

import math
import random
from collections import OrderedDict, deque
from dataclasses import dataclass

import numpy as np

from ..mapdata import EXTENT, LOD_TILE_ZOOM, POI_KINDS, MapFile, lonlat_to_world, world_to_lonlat

STREET_LOD = 3
TILE_Z = LOD_TILE_ZOOM[STREET_LOD]
EARTH_M = 40_075_016.686
M_PER_DEG_LAT = 111_320.0

DRIVABLE_MAX_CLASS = 6  # motorway .. service; tracks and footpaths are never driven
CLASS_SPEED = [29.0, 25.0, 19.0, 17.0, 15.0, 11.0, 5.0]  # m/s, roughly the speed limit
CLASS_PREFERENCE = [0.25, 0.5, 1.2, 1.3, 1.3, 1.0, 0.12]  # busy roads hear little; driveways are dull

CELL_M = 60.0
CELL_DLAT = CELL_M / M_PER_DEG_LAT
CELL_DLON = CELL_M / (M_PER_DEG_LAT * math.cos(math.radians(33.0)))  # fixed, so cells never move


def _flat_m(lat0: float, lon0: float, lat: float, lon: float) -> tuple[float, float]:
    """(east, north) metres from (lat0, lon0); fine over a few kilometres."""
    return (lon - lon0) * M_PER_DEG_LAT * math.cos(math.radians(lat0)), (lat - lat0) * M_PER_DEG_LAT


# --- map access ---------------------------------------------------------------------


class _Tile:
    """One street-level tile: drivable road pieces as segment arrays, plus buildings and
    businesses bucketed by network cell."""

    def __init__(self, data: dict, tx: int, ty: int):
        n = 2**TILE_Z
        scale = 1.0 / (EXTENT * n)
        origin = np.array([tx / n, ty / n])
        self.key = (tx, ty)
        self.pieces: list[tuple[int, np.ndarray]] = []  # (road class, (k, 2) world points)
        a, b, piece, index = [], [], [], []
        for rec in data.get("r", ()):
            cls, coords = rec[1], rec[3]
            if cls > DRIVABLE_MAX_CLASS or len(coords) < 4:
                continue
            pts = np.asarray(coords, dtype=np.float64).reshape(-1, 2) * scale + origin
            pi = len(self.pieces)
            self.pieces.append((cls, pts))
            a.append(pts[:-1])
            b.append(pts[1:])
            piece.append(np.full(len(pts) - 1, pi))
            index.append(np.arange(len(pts) - 1))
        if a:
            self.seg_a, self.seg_b = np.concatenate(a), np.concatenate(b)
            self.seg_piece, self.seg_index = np.concatenate(piece), np.concatenate(index)
        else:
            self.seg_a = self.seg_b = np.zeros((0, 2))
            self.seg_piece = self.seg_index = np.zeros(0, dtype=int)

        names = data.get("n", [])
        self.buildings: dict[tuple[int, int], list[tuple[float, float]]] = {}
        for rec in data.get("b", ()):
            ring = rec[1]
            if len(ring) < 6:
                continue
            x = tx / n + (sum(ring[0::2]) / (len(ring) // 2)) * scale
            y = ty / n + (sum(ring[1::2]) / (len(ring) // 2)) * scale
            lon, lat = world_to_lonlat(x, y)
            self.buildings.setdefault(cell_of(lat, lon), []).append((lat, lon))
        self.pois: dict[tuple[int, int], list[tuple[str, str, float, float]]] = {}
        for kind, name_i, x, y in data.get("i", ()):
            if name_i < 0:
                continue
            lon, lat = world_to_lonlat(tx / n + x * scale, ty / n + y * scale)
            self.pois.setdefault(cell_of(lat, lon), []).append((POI_KINDS[kind], names[name_i], lat, lon))


class MapIndex:
    """Lazily loaded street-level tiles of an offline map."""

    def __init__(self, mapfile: MapFile, max_tiles: int = 48):
        self.map = mapfile
        self.max_tiles = max_tiles
        self._tiles: OrderedDict[tuple[int, int], _Tile] = OrderedDict()

    def tile(self, tx: int, ty: int) -> _Tile:
        key = (tx, ty)
        t = self._tiles.get(key)
        if t is None:
            t = _Tile(self.map.tile(STREET_LOD, tx, ty) or {}, tx, ty)
            self._tiles[key] = t
            if len(self._tiles) > self.max_tiles:
                self._tiles.popitem(last=False)
        else:
            self._tiles.move_to_end(key)
        return t

    def _tiles_around(self, x: float, y: float, r: float) -> list[_Tile]:
        n = 2**TILE_Z
        x0, x1 = int((x - r) * n), int((x + r) * n)
        y0, y1 = int((y - r) * n), int((y + r) * n)
        return [self.tile(tx, ty) for tx in range(x0, x1 + 1) for ty in range(y0, y1 + 1)]

    def near(self, x: float, y: float, r: float):
        """Road segments within world distance r: yields (tile, segment, t along it, distance)."""
        p = np.array([x, y])
        for t in self._tiles_around(x, y, r):
            if not len(t.seg_a):
                continue
            d = t.seg_b - t.seg_a
            l2 = (d * d).sum(1)
            u = np.clip(((p - t.seg_a) * d).sum(1) / np.where(l2 > 0, l2, 1), 0, 1)
            dist = np.hypot(*(t.seg_a + u[:, None] * d - p).T)
            for i in np.nonzero(dist <= r)[0]:
                yield t, int(i), float(u[i]), float(dist[i])

    def buildings(self, cell: tuple[int, int]) -> list[tuple[float, float]]:
        return [b for t in self._cell_tiles(cell) for b in t.buildings.get(cell, ())]

    def pois(self, cell: tuple[int, int]) -> list[tuple[str, str, float, float]]:
        return [p for t in self._cell_tiles(cell) for p in t.pois.get(cell, ())]

    def _cell_tiles(self, cell: tuple[int, int]) -> list[_Tile]:
        n = 2**TILE_Z
        keys = set()
        for dy in (0, 1):
            for dx in (0, 1):
                x, y = lonlat_to_world((cell[1] + dx) * CELL_DLON, (cell[0] + dy) * CELL_DLAT)
                keys.add((int(x * n), int(y * n)))
        return [self.tile(*k) for k in keys]


def cell_of(lat: float, lon: float) -> tuple[int, int]:
    return math.floor(lat / CELL_DLAT), math.floor(lon / CELL_DLON)


# --- drivers ------------------------------------------------------------------------


class NoRoads(Exception):
    pass


class RoadDriver:
    """Drives along the map's roads: straight on at the end of a road where it can, the odd
    turn at junctions, short stops, and a pull back towards home past `roam_m`."""

    def __init__(self, index: MapIndex, lat: float, lon: float, rng: random.Random, roam_m: float = 5000):
        self.index, self.rng, self.roam_m = index, rng, roam_m
        self.m_per_world = EARTH_M * math.cos(math.radians(lat))
        self.home = lonlat_to_world(lon, lat)
        self.x, self.y = self.home
        self.heading, self.speed, self.cls = 0.0, 0.0, 5
        self._route: deque[tuple[float, float]] = deque()
        self._piece: tuple | None = None  # (tile key, piece index)
        self._piece_pts: np.ndarray | None = None
        self._pause = 0.0
        self._since_turn = 0.0
        self._pace = 1.0
        self._place()

    @property
    def lat(self) -> float:
        return world_to_lonlat(self.x, self.y)[1]

    @property
    def lon(self) -> float:
        return world_to_lonlat(self.x, self.y)[0]

    def _place(self) -> None:
        for radius in (300, 1500, 5000):
            found = list(self.index.near(*self.home, radius / self.m_per_world))
            local = [f for f in found if 2 <= f[0].pieces[f[0].seg_piece[f[1]]][0] <= 5] or found
            if local:
                t, i, u, _ = min(local, key=lambda f: f[3])
                options = self._options_on(t, i, u)
                if options:
                    self._take(self.rng.choice(options))
                    return
        raise NoRoads("no drivable roads near the start point")

    def _options_on(self, t: _Tile, i: int, u: float) -> list[tuple]:
        pi, si = int(t.seg_piece[i]), int(t.seg_index[i])
        cls, pts = t.pieces[pi]
        proj = pts[si] + u * (pts[si + 1] - pts[si])
        out = []
        for route in (np.vstack([proj, pts[si + 1 :]]), np.vstack([proj, pts[si::-1]])):
            direction = self._direction(route)
            if direction is not None:
                out.append(((t.key, pi), cls, route, direction, pts))
        return out

    def _direction(self, route: np.ndarray) -> float | None:
        """Heading (radians clockwise from north) over the first few metres, or None if too short."""
        start = route[0]
        for p in route[1:]:
            dx, dy = p - start
            if math.hypot(dx, dy) * self.m_per_world >= 3:
                return math.atan2(dx, -dy)
        return None

    def _options(self, exclude) -> list[tuple]:
        seen, out = set(), []
        for t, i, u, _ in self.index.near(self.x, self.y, 6 / self.m_per_world):
            for opt in self._options_on(t, i, u):
                if opt[0] == exclude:
                    continue
                sig = (opt[0], round(math.degrees(opt[3]) / 15))
                if sig not in seen:
                    seen.add(sig)
                    out.append(opt)
        return out

    def _take(self, option) -> None:
        key, cls, route, _, pts = option
        self._piece, self._piece_pts, self.cls = key, pts, cls
        self._route = deque(map(tuple, route[1:]))
        self._pace = self.rng.uniform(0.85, 1.1)

    def _choose(self, options, turning_only: bool):
        here = math.dist((self.x, self.y), self.home) * self.m_per_world
        weights = []
        for key, cls, route, direction, _ in options:
            turn = abs((direction - math.radians(self.heading) + math.pi) % (2 * math.pi) - math.pi)
            if turning_only and turn < math.radians(35):
                weights.append(0.0)  # the same road carrying on, not a junction
                continue
            w = 0.03 if turn > math.radians(150) else 1.0 if turn < math.radians(30) else 0.55
            w *= CLASS_PREFERENCE[cls]
            ahead = route[min(len(route) - 1, 3)]
            if here > self.roam_m:
                closer = math.dist(ahead, self.home) * self.m_per_world < here
                w *= 2.0 if closer else 0.15
            weights.append(w)
        if not options or sum(weights) <= 0:
            return None
        return self.rng.choices(options, weights)[0]

    def _at_vertex(self, end: bool) -> None:
        if end:
            choice = self._choose(self._options(self._piece), turning_only=False)
            if choice is None:  # dead end: turn around
                pts = self._piece_pts
                near_start = math.dist((self.x, self.y), pts[0]) < math.dist((self.x, self.y), pts[-1])
                self._route = deque(map(tuple, pts if near_start else pts[::-1]))
                return
        elif self._since_turn > 50 and self.rng.random() < 0.3:
            choice = self._choose(self._options(self._piece), turning_only=True)
            if choice is None:
                return
        else:
            return
        self._take(choice)
        self._since_turn = 0.0
        if self.rng.random() < 0.35:
            self._pause = self.rng.uniform(2, 20)  # stop sign or traffic light

    def step(self, dt: float) -> float:
        """Advance dt seconds; returns metres driven."""
        if self._pause > 0:
            self._pause -= dt
            self.speed = max(0.0, self.speed - 6 * dt)
            return self.speed * dt
        target = CLASS_SPEED[self.cls] * self._pace
        self.speed += max(-4 * dt, min(2.5 * dt, target - self.speed))
        remaining = moved = self.speed * dt
        while remaining > 1e-6:
            if not self._route:
                self._at_vertex(end=True)
                if not self._route:
                    break
            tx, ty = self._route[0]
            seg = math.hypot(tx - self.x, ty - self.y) * self.m_per_world
            if seg > 0.01:
                self.heading = math.degrees(math.atan2(tx - self.x, -(ty - self.y))) % 360
            if seg <= remaining:
                self.x, self.y = tx, ty
                remaining -= seg
                self._since_turn += seg
                self._route.popleft()
                self._at_vertex(end=not self._route)
                if self._pause > 0:
                    break
            else:
                f = remaining / seg
                self.x += (tx - self.x) * f
                self.y += (ty - self.y) * f
                self._since_turn += remaining
                remaining = 0.0
        return moved - remaining


class GridDriver:
    """Fallback without map roads: a square street grid around the start point."""

    BLOCK_M = 150.0

    def __init__(self, lat: float, lon: float, rng: random.Random, roam_m: float = 2500):
        self.home_lat, self.home_lon, self.rng, self.roam_m = lat, lon, rng, roam_m
        self.east = self.north = 0.0
        self.heading = rng.choice([0.0, 90.0, 180.0, 270.0])
        self.speed = 0.0
        self._pause = 0.0

    @property
    def lat(self) -> float:
        return self.home_lat + self.north / M_PER_DEG_LAT

    @property
    def lon(self) -> float:
        return self.home_lon + self.east / (M_PER_DEG_LAT * math.cos(math.radians(self.home_lat)))

    def step(self, dt: float) -> float:
        if self._pause > 0:
            self._pause -= dt
            self.speed = 0.0
            return 0.0
        self.speed = 12.0
        dist = self.speed * dt
        a = math.radians(self.heading)
        before = (self.east // self.BLOCK_M, self.north // self.BLOCK_M)
        self.east += math.sin(a) * dist
        self.north += math.cos(a) * dist
        after = (self.east // self.BLOCK_M, self.north // self.BLOCK_M)
        if before != after:  # crossed a street
            self.east = round(self.east / self.BLOCK_M) * self.BLOCK_M if self.heading in (90.0, 270.0) else self.east
            self.north = round(self.north / self.BLOCK_M) * self.BLOCK_M if self.heading in (0.0, 180.0) else self.north
            far = math.hypot(self.east, self.north) > self.roam_m
            if far:
                home = math.degrees(math.atan2(-self.east, -self.north)) % 360
                self.heading = min((0.0, 90.0, 180.0, 270.0), key=lambda h: abs((h - home + 180) % 360 - 180))
            elif self.rng.random() < 0.35:
                self.heading = (self.heading + self.rng.choice([90.0, -90.0])) % 360
                if self.rng.random() < 0.3:
                    self._pause = self.rng.uniform(2, 12)
        return dist


def make_driver(index: MapIndex | None, lat: float, lon: float, rng: random.Random):
    if index is not None:
        w, s, e, n = index.map.bounds
        if w <= lon <= e and s <= lat <= n:
            try:
                return RoadDriver(index, lat, lon, rng)
            except NoRoads:
                pass
    return GridDriver(lat, lon, rng)


# --- networks -----------------------------------------------------------------------


@dataclass
class SimDevice:
    mac: str
    phy: str  # "wifi" | "bt"
    name: str
    crypt: str  # Kismet crypt string, e.g. "WPA2 WPA2-PSK AES-CCMP"; "" for Bluetooth
    channel: str
    frequency: int  # kHz
    manuf: str
    dev_type: str  # Kismet type: "Wi-Fi AP", "BTLE", "BR/EDR"
    wps: bool
    lat: float
    lon: float
    power: float = 0.0  # dB relative to a typical device

    @property
    def key(self) -> str:
        return f"{self.phy}:{self.mac}"


SURNAMES = [
    "Smith", "Johnson", "Williams", "Brown", "Jones", "Miller", "Davis", "Wilson", "Anderson", "Taylor",
    "Thomas", "Moore", "Jackson", "Martin", "Lee", "Thompson", "White", "Harris", "Clark", "Lewis",
    "Robinson", "Walker", "Young", "Allen", "King", "Wright", "Hill", "Green", "Adams", "Baker", "Nelson",
    "Carter", "Mitchell", "Roberts", "Turner", "Phillips", "Campbell", "Parker", "Evans", "Edwards",
]
FUNNY = [
    "FBI Surveillance Van", "Pretty Fly for a WiFi", "Bill Wi the Science Fi", "Get Off My LAN",
    "It Hurts When IP", "The LAN Before Time", "Silence of the LANs", "Abraham Linksys", "Tell My WiFi Love Her",
    "Hide Yo Kids Hide Yo WiFi", "Loading...", "Virus.exe", "No Free WiFi Here", "Mom Use This One",
    "Wu-Tang LAN", "404 Network Unavailable", "Drop It Like Its Hotspot", "Martin Router King",
]
ROUTERS = [  # (vendor, OUI prefixes)
    ("Arris", ["00:1D:D0", "84:A0:6E", "E8:ED:05"]),
    ("Sagemcom", ["2C:E4:12", "7C:8B:CA", "C0:D7:AA"]),
    ("Netgear", ["9C:3D:CF", "A0:40:A0", "B0:39:56"]),
    ("TP-Link", ["50:C7:BF", "98:DA:C4", "60:A4:B7"]),
    ("Linksys", ["C4:41:1E", "E8:9F:80"]),
    ("ASUSTek", ["04:D9:F5", "2C:FD:A1"]),
    ("eero", ["F8:BB:BF", "30:57:8E"]),
    ("Google", ["F4:F5:D8", "3C:28:6D"]),
    ("Humax", ["E4:5D:51"]),
]
PRINTERS = ["HP OfficeJet Pro 8710", "HP ENVY 6000", "HP DeskJet 2700", "Canon TS3300", "Brother HL-L2350DW"]
BT_HOME = ["[TV] Samsung Q70 Series (55)", "[TV] Samsung 7 Series (65)", "LG webOS TV", "Roku Ultra", "Sonos One",
           "JBL Flip 5", "Echo Dot-4K2", "Bose Home Speaker 500", "Fire TV Stick", "HP ENVY 6000 series"]
BT_PASSING = ["", "", "", "", "", "", "Galaxy S23", "Galaxy Buds2 Pro", "Tile", "Apple Watch", "Fitbit Charge 5",
              "Ford SYNC", "Uconnect", "CAR MULTIMEDIA", "Toyota", "My Car", "Chevy MyLink", "JBL Charge 4", "LE-Bose QC45",
              "Pixel 8", "Beats Studio Buds", "ELK-BLEDOM", "Govee_H6159", "[AV] Samsung Soundbar"]
BT_VENDORS = ["Apple", "Samsung Electronics", "Unknown", "Unknown", "Google", "Bose", "Harman", "Tile"]
BUSINESS_SUFFIX = [" Guest", " WiFi", "-Guest", " Free WiFi", "_Public"]

WPA2 = "WPA2 WPA2-PSK AES-CCMP"
CHANNELS_24 = [1, 6, 11, 1, 6, 11, 3, 9]
CHANNELS_5 = [36, 40, 44, 48, 149, 153, 157, 161, 165]


def _freq(channel: int) -> int:
    return (2407 + 5 * channel) * 1000 if channel <= 14 else (5000 + 5 * channel) * 1000


def _mac(rng: random.Random, prefix: str | None = None) -> str:
    tail = [rng.randrange(256) for _ in range(3 if prefix else 6)]
    if prefix is None:
        tail[0] = (tail[0] | 0x02) & 0xFE  # random BLE-style address: locally administered, unicast
    return ":".join(filter(None, [prefix, ":".join(f"{b:02X}" for b in tail)]))


def _bump(mac: str, n: int) -> str:
    value = (int(mac.replace(":", ""), 16) + n) & 0xFFFFFFFFFFFF
    return ":".join(f"{(value >> s) & 0xFF:02X}" for s in range(40, -1, -8))


def _hex(rng: random.Random, n: int, upper=True) -> str:
    s = "".join(rng.choice("0123456789abcdef") for _ in range(n))
    return s.upper() if upper else s


class World:
    """Every simulated network, generated on demand per cell from a fixed seed."""

    def __init__(self, index: MapIndex | None, seed: int = 0):
        self.index, self.seed = index, seed
        self._cells: OrderedDict[tuple[int, int], list[SimDevice]] = OrderedDict()

    def around(self, lat: float, lon: float, radius_m: float) -> list[SimDevice]:
        cy, cx = cell_of(lat, lon)
        ry = int(radius_m / CELL_M) + 1
        rx = int(radius_m / (M_PER_DEG_LAT * math.cos(math.radians(lat)) * CELL_DLON)) + 1
        out = []
        for y in range(cy - ry, cy + ry + 1):
            for x in range(cx - rx, cx + rx + 1):
                out.extend(self.cell((y, x)))
        return out

    def cell(self, cell: tuple[int, int]) -> list[SimDevice]:
        devices = self._cells.get(cell)
        if devices is None:
            devices = self._generate(cell)
            self._cells[cell] = devices
            if len(self._cells) > 20_000:
                self._cells.popitem(last=False)
        return devices

    def _generate(self, cell: tuple[int, int]) -> list[SimDevice]:
        cy, cx = cell
        rng = random.Random((cy * 1_000_003 + cx) * 7919 + self.seed)
        buildings = self.index.buildings(cell) if self.index else []
        pois = self.index.pois(cell) if self.index else []
        if buildings:
            homes = [b for b in buildings if rng.random() < 0.35]  # sheds, garages, empty units
        else:
            count = rng.choices([0, 1, 2, 3], [78, 14, 6, 2])[0]
            homes = [((cy + rng.random()) * CELL_DLAT, (cx + rng.random()) * CELL_DLON) for _ in range(count)]
        out: list[SimDevice] = []
        for lat, lon in homes:
            out.extend(self._household(rng, lat, lon))
        for kind, name, lat, lon in pois:
            out.extend(self._business(rng, kind, name, lat, lon))
        return out

    def _household(self, rng: random.Random, lat: float, lon: float) -> list[SimDevice]:
        lat += rng.gauss(0, 4) / M_PER_DEG_LAT
        lon += rng.gauss(0, 4) / (M_PER_DEG_LAT * math.cos(math.radians(lat)))
        isp = rng.choices(["att", "spectrum", "xfinity", "windstream", "own"], [30, 25, 20, 7, 18])[0]
        vendor, prefixes = {
            "att": ROUTERS[1], "spectrum": ROUTERS[0], "xfinity": ROUTERS[0], "windstream": ROUTERS[8],
        }.get(isp) or rng.choice(ROUTERS[2:8])
        mac = _mac(rng, rng.choice(prefixes))
        roll = rng.random()
        if roll < 0.07:
            ssid = ""  # hidden
        elif roll < 0.12:
            ssid = rng.choice(FUNNY)
        elif roll < 0.27:
            name = rng.choice(SURNAMES)
            ssid = rng.choice([f"The {name}s", f"{name} Family", f"{name}_Home", f"{name}WiFi", f"{name}-Guest"])
        elif isp == "att":
            ssid = rng.choice([f"ATT{_hex(rng, 7)}", f"ATT-WIFI-{_hex(rng, 4)}"])
        elif isp == "spectrum":
            ssid = rng.choice([f"MySpectrumWiFi{_hex(rng, 2, False)}-2G", f"SpectrumSetup-{_hex(rng, 2)}"])
        elif isp == "xfinity":
            ssid = rng.choice([f"HOME-{_hex(rng, 4)}", f"Xfinity-{_hex(rng, 4)}"])
        elif isp == "windstream":
            ssid = f"Windstream{rng.randrange(1000, 9999)}"
        else:
            ssid = {"Netgear": f"NETGEAR{rng.randrange(10, 99)}", "TP-Link": f"TP-Link_{_hex(rng, 4)}",
                    "Linksys": f"Linksys{rng.randrange(10000, 99999)}", "ASUSTek": f"ASUS_{_hex(rng, 2)}",
                    "eero": rng.choice(SURNAMES) + " eero", "Google": "Google Nest"}.get(vendor, "HOME")
        crypt = rng.choices([WPA2, "WPA2 WPA2-PSK WPA3 WPA3-SAE AES-CCMP", "Open", "WEP", "WPA1 WPA1-PSK TKIP"],
                            [80, 12, 3, 1, 2])[0]
        if ssid.startswith("SpectrumSetup"):
            crypt = WPA2
        wps = crypt != "Open" and rng.random() < 0.4
        power = rng.uniform(-6, 3)
        ch24 = rng.choice(CHANNELS_24)
        out = [SimDevice(mac, "wifi", ssid, crypt, str(ch24), _freq(ch24), vendor, "Wi-Fi AP", wps, lat, lon, power)]
        if rng.random() < 0.4:  # dual-band router: a second BSSID on 5 GHz
            ch5 = rng.choice(CHANNELS_5)
            name5 = ssid.replace("-2G", "-5G") if ssid.endswith("-2G") else (ssid + rng.choice(["", "", "-5G", "_5G"]) if ssid else "")
            out.append(SimDevice(_bump(mac, 1), "wifi", name5, crypt, str(ch5), _freq(ch5), vendor, "Wi-Fi AP", wps,
                                 lat, lon, power - 6))
        if isp == "xfinity" and rng.random() < 0.6:
            out.append(SimDevice(_bump(mac, 2), "wifi", "xfinitywifi", "Open", str(ch24), _freq(ch24), vendor,
                                 "Wi-Fi AP", False, lat, lon, power))
        if rng.random() < 0.1:
            ch = rng.choice(CHANNELS_24)
            out.append(SimDevice(_mac(rng, "A0:D3:C1"), "wifi", f"DIRECT-{_hex(rng, 2)}-{rng.choice(PRINTERS)}", WPA2,
                                 str(ch), _freq(ch), "Hewlett Packard", "Wi-Fi AP", True, lat, lon, -8))
        if rng.random() < 0.05:
            out.append(SimDevice(_mac(rng, "B0:A7:37"), "wifi", f"DIRECT-roku-{rng.randrange(100, 999)}", WPA2,
                                 "6", _freq(6), "Roku", "Wi-Fi AP", False, lat, lon, -8))
        if rng.random() < 0.25:
            bt_type = rng.choice(["BTLE", "BTLE", "BR/EDR"])
            out.append(SimDevice(_mac(rng), "bt", rng.choice(BT_HOME), "", "FHSS", 2402000, rng.choice(BT_VENDORS),
                                 bt_type, False, lat, lon, -4))
        return out

    def _business(self, rng: random.Random, kind: str, name: str, lat: float, lon: float) -> list[SimDevice]:
        if rng.random() > 0.6:
            return []
        clean = name.replace(",", "")[:24]
        vendor, prefixes = rng.choice([("Cisco Meraki", ["00:18:0A", "E0:55:3D"]), ("Ubiquiti", ["24:5A:4C", "78:8A:20"]),
                                       ("Aruba", ["00:0B:86", "20:4C:03"])])
        mac = _mac(rng, rng.choice(prefixes))
        ch = rng.choice(CHANNELS_24 + CHANNELS_5)
        out = [SimDevice(mac, "wifi", clean + rng.choice(BUSINESS_SUFFIX), rng.choice(["Open", "Open", WPA2]), str(ch),
                         _freq(ch), vendor, "Wi-Fi AP", False, lat, lon, 3)]
        if rng.random() < 0.6:
            out.append(SimDevice(_bump(mac, 1), "wifi", rng.choice(["", f"{clean[:12]}-Staff", "POS-NET"]),
                                 "WPA2 WPA2-EAP AES-CCMP", str(ch), _freq(ch), vendor, "Wi-Fi AP", False, lat, lon, 3))
        if kind in ("shop", "amenity") and rng.random() < 0.2:
            out.append(SimDevice(_mac(rng), "bt", "", "", "FHSS", 2402000, "Unknown", "BTLE", False, lat, lon, 0))
        return out


# --- radio ----------------------------------------------------------------------------


class Scanner:
    """What the capture radios pick up each second at the car's position."""

    WIFI_POWER, BT_POWER = -22.0, -42.0  # dBm at 1 m
    PATH_LOSS = 30.0  # 10 x exponent: suburban, through walls and trees
    FLOOR = -94.0

    def __init__(self, world: World, rng: random.Random):
        self.world, self.rng = world, rng
        self.np_rng = np.random.default_rng(rng.randrange(2**32))
        self._candidates: list[SimDevice] = []
        self._pos = np.zeros((0, 2))
        self._power = np.zeros(0)
        self._at: tuple[float, float] | None = None
        self._passing: list[tuple[SimDevice, float, float, float]] = []  # (device, east, north, expires)

    def _refresh(self, lat: float, lon: float) -> None:
        self._candidates = self.world.around(lat, lon, 280)
        self._pos = np.array([(d.lat, d.lon) for d in self._candidates]).reshape(-1, 2)
        base = np.array([self.WIFI_POWER if d.phy == "wifi" else self.BT_POWER for d in self._candidates])
        self._power = base + np.array([d.power for d in self._candidates]).reshape(-1)
        self._at = (lat, lon)

    def scan(self, t: float, lat: float, lon: float, speed: float) -> list[tuple[SimDevice, int]]:
        """Devices heard during one second at (lat, lon), with their signal in dBm."""
        if self._at is None or math.hypot(*_flat_m(self._at[0], self._at[1], lat, lon)) > 60:
            self._refresh(lat, lon)
        hits: list[tuple[SimDevice, int]] = []
        if self._candidates:
            north = (self._pos[:, 0] - lat) * M_PER_DEG_LAT
            east = (self._pos[:, 1] - lon) * M_PER_DEG_LAT * math.cos(math.radians(lat))
            dist = np.maximum(3.0, np.hypot(north, east))
            rssi = self._power - self.PATH_LOSS * np.log10(dist) + self.np_rng.normal(0, 4, len(dist))
            prob = np.clip((rssi - self.FLOOR) / 20, 0, 1) * 0.7
            for i in np.nonzero(self.np_rng.random(len(dist)) < prob)[0]:
                hits.append((self._candidates[i], int(max(-99, min(-20, rssi[i])))))
        hits.extend(self._passing_devices(t, lat, speed))
        return hits

    def _passing_devices(self, t: float, lat: float, speed: float) -> list[tuple[SimDevice, int]]:
        """Phones, watches and cars nearby for a little while; more of them in slow traffic."""
        rng = self.rng
        self._passing = [p for p in self._passing if p[3] > t]
        if rng.random() < (0.08 if speed < 5 else 0.04):
            name = rng.choice(BT_PASSING)
            bt_type = "BR/EDR" if name and rng.random() < 0.3 else "BTLE"
            dev = SimDevice(_mac(rng), "bt", name, "", "FHSS", 2402000, rng.choice(BT_VENDORS), bt_type, False, 0, 0)
            self._passing.append((dev, rng.uniform(-25, 25), rng.uniform(-25, 25), t + rng.uniform(8, 90)))
        out = []
        for dev, east, north, _ in self._passing:
            dist = max(3.0, math.hypot(east, north))
            rssi = self.BT_POWER - self.PATH_LOSS * math.log10(dist) + rng.gauss(0, 4)
            if rssi > self.FLOOR and rng.random() < 0.7:
                out.append((dev, int(rssi)))
        return out
