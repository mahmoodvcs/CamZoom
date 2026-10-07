"""CamZoom: zooms/crops your webcam (optionally following your face) and sends it to the CamZoom Camera.

Pick "CamZoom Camera" as the camera in Teams. Runs in the system tray; see README.md.
"""

import ctypes
import json
import logging
import os
import queue
import sys
import threading
import time
import tkinter as tk
import winreg
from logging.handlers import RotatingFileHandler
from pathlib import Path
from tkinter import ttk

# Must be set before cv2 is imported: avoids a multi-second Media Foundation startup delay.
os.environ.setdefault("OPENCV_VIDEOIO_MSMF_ENABLE_HW_TRANSFORMS", "0")

import cv2
import keyboard
import numpy as np
import pystray
from PIL import Image, ImageDraw, ImageTk
from pygrabber.dshow_graph import FilterGraph

import vcam

APP = "CamZoom"
# Bundled data files live next to this script, or in the unpacked bundle when frozen by PyInstaller.
APP_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
DATA_DIR = Path(os.environ["APPDATA"]) / APP
CONFIG_PATH = DATA_DIR / "config.json"
LOG_PATH = DATA_DIR / "camzoom.log"
MODEL_PATH = APP_DIR / "models" / "face_detection_yunet_2023mar.onnx"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
PREVIEW_WINDOW = "CamZoom preview"

OUT_W, OUT_H, FPS = 1280, 720, 30
CAP_W, CAP_H = 1920, 1080  # capture larger than the output so zoomed-in video stays sharp
MIN_ZOOM, MAX_ZOOM, ZOOM_STEP = 1.0, 4.0, 1.1
DETECT_EVERY = 5  # run face detection on every Nth frame (6x per second is plenty for smooth tracking)
WATCH_INTERVAL = 2.0  # seconds between checks for apps using the virtual camera
RELEASE_AFTER = 2  # consecutive checks with no app before the webcam is turned off (rides out device switches)

DEFAULTS = {
    "camera": None,  # source camera name; None = first non-virtual camera
    "zoom": 1.5,
    "tracking": True,
    "auto": True,  # only turn the webcam on while some app is using the virtual camera
    "brightness": 0,  # how much to brighten the whole picture, 0-100
    "shadows": 0,  # how much to brighten dark areas, 0-100
    "hotkeys": {
        "zoom_in": "ctrl+alt+=",
        "zoom_out": "ctrl+alt+-",
        "zoom_reset": "ctrl+alt+0",
        "toggle_tracking": "ctrl+alt+f",
        "toggle_camera": "ctrl+alt+c",
    },
}

log = logging.getLogger(APP)

# OpenCV's worker threads busy-wait; on these small per-frame jobs they burned ~3x the CPU for little speedup.
cv2.setNumThreads(1)


def load_config():
    cfg = json.loads(json.dumps(DEFAULTS))
    try:
        saved = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        cfg["hotkeys"].update(saved.pop("hotkeys", {}))
        cfg.update(saved)
    except FileNotFoundError:
        pass
    except Exception:
        log.exception("Could not read %s; using defaults", CONFIG_PATH)
    return cfg


def save_config(cfg):
    try:
        CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    except Exception:
        log.exception("Could not save %s", CONFIG_PATH)


def list_cameras():
    """Camera names in Media Foundation index order."""
    try:
        names = FilterGraph().get_input_devices()
    except Exception:
        log.exception("Could not enumerate cameras")
        return []
    # DirectShow-only virtual cameras (ours, OBS's) are invisible to Media Foundation, so they'd shift the indexes.
    return [n for n in names if n != vcam.CAMERA_NAME and "OBS" not in n]


def hotkey_label(combo):
    return "+".join(part.capitalize() if len(part) > 1 else part.upper() for part in combo.split("+"))


# --- Start with Windows -------------------------------------------------------------------------

def startup_command():
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --autostart'
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    return f'"{pythonw}" "{Path(__file__).resolve()}" --autostart'


def startup_enabled():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, APP)
        return True
    except OSError:
        return False


def set_startup(enabled):
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, APP, 0, winreg.REG_SZ, startup_command())
        else:
            try:
                winreg.DeleteValue(key, APP)
            except FileNotFoundError:
                pass


# --- Video ----------------------------------------------------------------------------------------

class FaceTracker:
    DETECT_W = 480

    def __init__(self):
        self.detector = None
        if MODEL_PATH.exists():
            self.detector = cv2.FaceDetectorYN.create(str(MODEL_PATH), "", (320, 320), 0.7)
        else:
            log.warning("Face model missing (%s); face tracking disabled", MODEL_PATH)

    def find(self, frame):
        """Center (x, y) of the largest face, in frame pixels, or None."""
        if self.detector is None:
            return None
        h, w = frame.shape[:2]
        scale = self.DETECT_W / w
        small = cv2.resize(frame, (self.DETECT_W, round(h * scale)), interpolation=cv2.INTER_AREA)
        self.detector.setInputSize((small.shape[1], small.shape[0]))
        _, faces = self.detector.detect(small)
        if faces is None or len(faces) == 0:
            return None
        x, y, fw, fh = max(faces, key=lambda f: f[2] * f[3])[:4] / scale
        return x + fw / 2, y + fh / 2


class Framer:
    """Moves and zooms a crop window smoothly over the camera frame."""

    EASE_PAN = 0.06  # fraction of the remaining distance moved per frame
    EASE_ZOOM = 0.2
    DEADZONE = 0.08  # how far (fraction of crop size) the face may drift before the view follows
    HEADROOM = 0.12  # keeps the face a little above center
    FACE_TIMEOUT = 3.0  # seconds without a face before drifting back to center

    def __init__(self):
        self.zoom = None

    def see_face(self, face):
        if face is None or self.zoom is None:
            return
        self.face_seen = time.monotonic()
        fx, fy = face[0], face[1] + self.crop_h * self.HEADROOM
        if abs(fx - self.tx) > self.crop_w * self.DEADZONE or abs(fy - self.ty) > self.crop_h * self.DEADZONE:
            self.tx, self.ty = fx, fy

    def render(self, frame, target_zoom, tracking):
        fh, fw = frame.shape[:2]
        if fw * OUT_H >= fh * OUT_W:
            base_w, base_h = fh * OUT_W / OUT_H, fh
        else:
            base_w, base_h = fw, fw * OUT_H / OUT_W
        if self.zoom is None:
            self.zoom = target_zoom
            self.cx = self.tx = fw / 2
            self.cy = self.ty = fh / 2
            self.face_seen = 0.0

        self.zoom += (target_zoom - self.zoom) * self.EASE_ZOOM
        cw, ch = base_w / self.zoom, base_h / self.zoom
        self.crop_w, self.crop_h = cw, ch

        if not tracking or time.monotonic() - self.face_seen > self.FACE_TIMEOUT:
            self.tx, self.ty = fw / 2, fh / 2
        self.cx += (self.tx - self.cx) * self.EASE_PAN
        self.cy += (self.ty - self.cy) * self.EASE_PAN
        self.cx = min(max(self.cx, cw / 2), fw - cw / 2)
        self.cy = min(max(self.cy, ch / 2), fh - ch / 2)

        # One affine warp does crop + scale with sub-pixel positioning, so slow pans don't judder.
        s = OUT_W / cw
        m = np.float32([[s, 0, -(self.cx - cw / 2) * s], [0, s, -(self.cy - ch / 2) * s]])
        return cv2.warpAffine(frame, m, (OUT_W, OUT_H), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)


class Brighten:
    """Brightens the whole picture the way a longer exposure would: it multiplies the light, then rolls off the
    highlights (extended Reinhard) so bright areas compress instead of turning white. Unlike Windows' camera
    Brightness setting, which adds a constant and turns black into gray, black stays black. ~0.5 ms per frame.
    """

    MAX_STOPS = 3  # at full strength the light is multiplied by 2**3

    def __init__(self):
        self.amount = None
        self.lut = None

    def apply(self, img, amount):
        """amount: 0 (off) to 100."""
        if amount <= 0:
            return img
        if amount != self.amount:
            self.amount = amount
            gain = 2 ** (self.MAX_STOPS * amount / 100)
            light = (np.arange(256) / 255) ** 2.2 * gain  # pixel values are gamma-encoded; exposure acts on light
            light = light * (1 + light / gain ** 2) / (1 + light)  # maps 0..gain back to 0..1, unchanged at gain 1
            self.lut = np.round(light ** (1 / 2.2) * 255).astype(np.uint8)
        return cv2.LUT(img, self.lut)


class ShadowLift:
    """Brightens the dark parts of the picture and leaves the bright parts alone, like a photo editor's Shadows slider.

    How much a pixel is brightened depends on how dark its surroundings are, not on the pixel itself, so detail and
    contrast within dark areas are kept. The surroundings come from an edge-preserving blur of a small copy of the
    picture, so a dark face in front of a bright window is brightened evenly right up to its edge. ~2 ms per frame.
    """

    MAX_GAIN = 4.0  # how much the darkest areas are brightened at full strength
    SMALL = (OUT_W // 8, OUT_H // 8)

    def __init__(self):
        self.amount = None
        self.lut = None

    def apply(self, img, amount):
        """amount: 0 (off) to 100."""
        if amount <= 0:
            return img
        if amount != self.amount:
            self.amount = amount
            level = np.arange(256) / 255
            gain = 1 + (self.MAX_GAIN - 1) * amount / 100 * (1 - level) ** 6  # falls off fast: midtones barely change
            self.lut = np.round(gain * 64).astype(np.uint8)  # fixed point, so the multiply below stays in fast uint8
        small = cv2.resize(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), self.SMALL, interpolation=cv2.INTER_AREA)
        small = cv2.bilateralFilter(small, 13, 30, 6)
        gain = cv2.resize(cv2.LUT(small, self.lut), (img.shape[1], img.shape[0]), interpolation=cv2.INTER_LINEAR)
        return cv2.multiply(img, cv2.cvtColor(gain, cv2.COLOR_GRAY2BGR), scale=1 / 64)


def placeholder(*lines):
    img = np.full((OUT_H, OUT_W, 3), 32, np.uint8)
    font, sizes = cv2.FONT_HERSHEY_SIMPLEX, [1.4] + [0.9] * (len(lines) - 1)
    y = OUT_H // 2 - 30 * (len(lines) - 1)
    for line, size in zip(lines, sizes):
        (tw, th), _ = cv2.getTextSize(line, font, size, 2)
        cv2.putText(img, line, ((OUT_W - tw) // 2, y), font, size, (220, 220, 220), 2, cv2.LINE_AA)
        y += th + 30
    return img


# --- Settings window ------------------------------------------------------------------------------

class SettingsWindow:
    """A small Tk window. Tk may only be used from the thread that created it, so it gets a thread of its own,
    started on first use; closing the window just hides it, and other threads talk to it through a queue."""

    SAVE_DELAY = 500  # ms after the last slider move before the config is written

    def __init__(self, app):
        self.app = app
        self.requests = queue.SimpleQueue()
        self.thread = None

    def show(self):
        self.requests.put("show")
        if self.thread is None:
            self.thread = threading.Thread(target=self.run, name="settings", daemon=True)
            self.thread.start()

    def close(self):
        if self.thread is not None:
            self.requests.put("quit")
            self.thread.join(timeout=2)  # let Tk shut down in its own thread rather than be cut off at exit

    def run(self):
        # Otherwise Windows draws the window at 96 DPI and stretches it, which looks blurry on high-DPI screens.
        try:
            ctypes.windll.user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-2))  # SYSTEM_AWARE
        except AttributeError:
            pass
        self.root = root = tk.Tk()
        root.withdraw()
        root.title(f"{APP} settings")
        root.resizable(False, False)
        icon = ImageTk.PhotoImage(make_icon("live"))
        root.iconphoto(True, icon)
        root.protocol("WM_DELETE_WINDOW", root.withdraw)
        self.save_job = None
        self.sliders = {}

        frame = ttk.Frame(root, padding=16)
        frame.grid()
        hk = {k: hotkey_label(v) for k, v in self.app.cfg["hotkeys"].items()}
        self.add_slider(frame, 0, "zoom", "Zoom",
                        f"How far to zoom in; 1× shows the whole picture. Hotkeys:\n"
                        f"{hk['zoom_in']} in, {hk['zoom_out']} out, {hk['zoom_reset']} reset.",
                        low=MIN_ZOOM, high=MAX_ZOOM, snap=lambda v: round(v, 2), fmt=lambda v: f"{v:.1f}×")
        self.add_slider(frame, 3, "brightness", "Brightness",
                        "Brightens the whole picture, like a longer exposure. Unlike\n"
                        "Windows' camera brightness, black stays black. 0 = off.")
        self.add_slider(frame, 6, "shadows", "Brighten shadows",
                        "Brightens dark areas, like a face lit from behind,\n"
                        "without changing bright areas. 0 = off.")
        self.preview = tk.BooleanVar(value=self.app.preview)
        ttk.Checkbutton(frame, text="Show preview", variable=self.preview,
                        command=lambda: self.app.set_preview(self.preview.get())).grid(row=9, column=0, sticky="w")
        ttk.Button(frame, text="Close", command=root.withdraw).grid(row=9, column=1, sticky="e")

        self.poll()
        root.mainloop()
        root.destroy()
        self.root = self.preview = self.sliders = None  # Tk objects must also be freed on this thread

    def add_slider(self, frame, row, key, title, hint, low=0, high=100, snap=round, fmt=str):
        """A slider for cfg[key], with its value shown on the right; takes three grid rows.
        snap rounds the slider position to a stored value, fmt turns a value into the text shown."""
        ttk.Label(frame, text=title).grid(row=row, column=0, sticky="w")
        value_label = ttk.Label(frame, text=fmt(self.app.cfg[key]), width=5, anchor="e")
        value_label.grid(row=row, column=1, sticky="e")
        scale = ttk.Scale(frame, from_=low, to=high, length=360,
                          command=lambda value: self.on_slider(key, value_label, snap(float(value)), fmt))
        scale.set(self.app.cfg[key])
        self.sliders[key] = scale, value_label, snap, fmt
        scale.grid(row=row + 1, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        ttk.Label(frame, foreground="gray", text=hint).grid(row=row + 2, column=0, columnspan=2, sticky="w",
                                                            pady=(4, 12))

    def on_slider(self, key, value_label, value, fmt):
        if value == self.app.cfg[key]:
            return
        self.app.cfg[key] = value  # the video thread picks this up on its next frame
        value_label.config(text=fmt(value))
        if self.save_job:
            self.root.after_cancel(self.save_job)
        self.save_job = self.root.after(self.SAVE_DELAY, self.app.save_and_refresh)  # refresh: tray shows the zoom

    def poll(self):
        while not self.requests.empty():
            if self.requests.get() == "quit":
                self.root.quit()
                return
            self.root.deiconify()
            self.root.lift()
            self.root.focus_force()
        if self.preview.get() != self.app.preview:  # changed from the tray menu or the preview's close button
            self.preview.set(self.app.preview)
        for key, (scale, value_label, snap, fmt) in self.sliders.items():  # zoom also changes from hotkeys and tray
            value = self.app.cfg[key]
            if snap(scale.get()) != value:
                scale.set(value)
                value_label.config(text=fmt(value))
        self.root.after(100, self.poll)


# --- App ------------------------------------------------------------------------------------------

class App:
    def __init__(self, autostart):
        self.cfg = load_config()
        # Master switch. Without auto mode, start with it off when launched at login, so the webcam isn't on all day.
        self.enabled = self.cfg["auto"] or not autostart
        self.users = {}  # {pid: exe} of apps currently using the virtual camera
        self.preview = False
        self.cameras = list_cameras()
        self.reopen = threading.Event()
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.tracker = FaceTracker()
        self.brighten = Brighten()
        self.shadow_lift = ShadowLift()
        self.settings = SettingsWindow(self)
        self.icon = pystray.Icon(APP, make_icon(self.state), APP, self.build_menu())
        self._preview_open = False
        self._warned_vcam = False

    @property
    def live(self):
        """Whether the webcam should be on right now. The preview window counts as an app using the camera."""
        return self.enabled and (bool(self.users) or self.preview or not self.cfg["auto"])

    @property
    def state(self):
        return "live" if self.live else "waiting" if self.enabled else "off"

    # state changes (called from tray menu and hotkey threads)

    def toggle_camera(self):
        self.enabled = not self.enabled
        self.refresh()

    def toggle_auto(self):
        self.cfg["auto"] = not self.cfg["auto"]
        self.save_and_refresh()

    def toggle_tracking(self):
        self.cfg["tracking"] = not self.cfg["tracking"]
        self.save_and_refresh()

    def change_zoom(self, factor):
        zoom = 1.0 if factor is None else self.cfg["zoom"] * factor
        self.cfg["zoom"] = round(min(max(zoom, MIN_ZOOM), MAX_ZOOM), 2)
        self.save_and_refresh()

    def select_camera(self, name):
        self.cfg["camera"] = name
        self.reopen.set()
        self.save_and_refresh()

    def refresh_cameras(self):
        self.cameras = list_cameras()
        self.refresh()

    def toggle_preview(self):
        self.set_preview(not self.preview)

    def set_preview(self, on):
        self.preview = on
        self.refresh()

    def toggle_startup(self):
        try:
            set_startup(not startup_enabled())
        except OSError:
            log.exception("Could not change startup setting")
            self.icon.notify("Could not change the startup setting. See the log for details.", APP)
        self.refresh()

    def quit(self):
        self.stop.set()
        keyboard.unhook_all()
        self.settings.close()
        save_config(self.cfg)  # the settings window saves with a short delay; don't lose a change made just now
        self.icon.stop()

    def save_and_refresh(self):
        save_config(self.cfg)
        self.refresh()

    def refresh(self):
        with self.lock:
            state = self.state
            self.icon.icon = make_icon(state)
            if state == "live":
                tracking = "face tracking on" if self.cfg["tracking"] else "face tracking off"
                used_by = f"\nIn use by {', '.join(sorted(set(self.users.values())))}" if self.users else ""
                self.icon.title = f"{APP}: {self.cfg['zoom']:.1f}× zoom, {tracking}{used_by}"
            elif state == "waiting":
                self.icon.title = f"{APP}: webcam turns on when an app uses the CamZoom Camera"
            else:
                self.icon.title = f"{APP}: camera off"
            self.icon.update_menu()

    # tray menu

    def build_menu(self):
        item, sep = pystray.MenuItem, pystray.Menu.SEPARATOR
        hk = {k: hotkey_label(v) for k, v in self.cfg["hotkeys"].items()}

        def select(name):  # a closure, since pystray treats extra lambda parameters as (icon, item)
            return lambda: self.select_camera(name)

        def camera_items():
            current = self.camera_name()
            for name in self.cameras:
                yield item(name, select(name), checked=lambda _, n=name: n == current, radio=True)
            yield sep
            yield item("Refresh list", self.refresh_cameras)

        return pystray.Menu(
            item(f"Camera enabled\t{hk['toggle_camera']}", self.toggle_camera,
                 checked=lambda _: self.enabled, default=True),
            item("Only while an app is using it", self.toggle_auto, checked=lambda _: self.cfg["auto"]),
            item(f"Face tracking\t{hk['toggle_tracking']}", self.toggle_tracking,
                 checked=lambda _: self.cfg["tracking"], enabled=lambda _: self.tracker.detector is not None),
            sep,
            item(lambda _: f"Zoom: {self.cfg['zoom']:.1f}×", None, enabled=False),
            item(f"Zoom in\t{hk['zoom_in']}", lambda: self.change_zoom(ZOOM_STEP)),
            item(f"Zoom out\t{hk['zoom_out']}", lambda: self.change_zoom(1 / ZOOM_STEP)),
            item(f"Reset zoom\t{hk['zoom_reset']}", lambda: self.change_zoom(None)),
            sep,
            item("Source camera", pystray.Menu(camera_items)),
            item("Show preview", self.toggle_preview, checked=lambda _: self.preview),
            item("Settings...", self.settings.show),
            item("Start with Windows", self.toggle_startup, checked=lambda _: startup_enabled()),
            sep,
            item("Quit", self.quit),
        )

    def register_hotkeys(self):
        actions = {
            "zoom_in": lambda: self.change_zoom(ZOOM_STEP),
            "zoom_out": lambda: self.change_zoom(1 / ZOOM_STEP),
            "zoom_reset": lambda: self.change_zoom(None),
            "toggle_tracking": self.toggle_tracking,
            "toggle_camera": self.toggle_camera,
        }
        for name, action in actions.items():
            combo = self.cfg["hotkeys"].get(name)
            if not combo:
                continue
            try:
                keyboard.add_hotkey(combo, action)
            except Exception:
                log.exception("Invalid hotkey %r for %s", combo, name)

    # watcher thread

    def watch_loop(self):
        misses = 0
        while not self.stop.wait(WATCH_INTERVAL):
            if not (self.enabled and self.cfg["auto"]):
                misses, self.users = 0, {}  # not needed now; don't let stale results switch the webcam on later
                continue
            try:
                users = vcam.camera_users()
            except Exception:
                log.exception("Could not check which apps use the virtual camera")
                continue
            if not users and self.users:
                misses += 1
                if misses < RELEASE_AFTER:
                    continue
            misses = 0
            if users.keys() != self.users.keys():
                log.info("Virtual camera in use by: %s", ", ".join(sorted(set(users.values()))) or "nobody")
                self.users = users
                self.refresh()

    # video thread

    def camera_name(self):
        if self.cfg["camera"] in self.cameras:
            return self.cfg["camera"]
        real = [n for n in self.cameras if "virtual" not in n.lower()]
        return (real or self.cameras or [None])[0]

    def open_camera(self):
        name = self.camera_name()
        index = self.cameras.index(name) if name in self.cameras else 0
        cap = cv2.VideoCapture(index, cv2.CAP_MSMF)
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, CAP_W)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, CAP_H)
        cap.set(cv2.CAP_PROP_FPS, FPS)
        ok, frame = cap.read()
        if not ok:
            cap.release()
            log.warning("Could not open camera %r (index %d)", name, index)
            return None
        log.info("Opened camera %r (index %d) at %dx%d", name, index, frame.shape[1], frame.shape[0])
        return cap

    def video_loop(self):
        while not self.stop.is_set():
            try:
                with vcam.VirtualCamera(OUT_W, OUT_H, FPS) as camera:
                    log.info("Sending video to the %s", vcam.CAMERA_NAME)
                    self._warned_vcam = False
                    self.stream(camera)
            except Exception:
                if self.stop.is_set():
                    break
                log.exception("Virtual camera unavailable")
                if not self._warned_vcam:
                    self._warned_vcam = True
                    self.icon.notify("Can't start the virtual camera. See the log for details. Retrying...", APP)
                self.stop.wait(5)
        self.show_preview(None)

    def stream(self, camera):
        cap, framer, frame_no, retry_at = None, Framer(), 0, 0.0
        off_img = placeholder("Camera off", f"Press {hotkey_label(self.cfg['hotkeys']['toggle_camera'])} "
                                             "or click the CamZoom tray icon")
        starting_img = placeholder("Starting camera...")  # what an app sees until we notice it and open the webcam
        missing_img = placeholder("Camera not available", "Retrying...")
        try:
            while not self.stop.is_set():
                live = self.live
                if self.reopen.is_set() or not live:
                    self.reopen.clear()
                    if cap is not None:
                        cap.release()  # releases the webcam, so its light turns off
                        cap, framer, retry_at = None, Framer(), 0.0
                if not live or (cap is None and time.monotonic() < retry_at):
                    self.output(camera, missing_img if live else starting_img if self.enabled else off_img)
                    self.stop.wait(0.1)
                    continue
                if cap is None:
                    self.output(camera, starting_img)
                    cap = self.open_camera()
                    if cap is None:
                        retry_at = time.monotonic() + 3
                    continue

                ok, frame = cap.read()  # blocks until the camera delivers, which paces the loop
                if not ok:
                    log.warning("Camera stopped delivering frames; reopening")
                    cap.release()
                    cap, framer, retry_at = None, Framer(), time.monotonic() + 3
                    continue
                tracking = self.cfg["tracking"]
                img = framer.render(frame, self.cfg["zoom"], tracking)
                img = self.brighten.apply(img, self.cfg["brightness"])
                self.output(camera, self.shadow_lift.apply(img, self.cfg["shadows"]))
                if tracking and frame_no % DETECT_EVERY == 0:
                    framer.see_face(self.tracker.find(frame))
                frame_no += 1
        finally:
            if cap is not None:
                cap.release()

    def output(self, camera, img):
        camera.send(img)
        try:
            self.show_preview(img if self.preview else None)
        except cv2.error:  # a preview problem must never stop the video apps are receiving
            log.exception("Preview window error")
            self._preview_open = False

    def show_preview(self, img):
        if img is not None:
            if self._preview_open and cv2.getWindowProperty(PREVIEW_WINDOW, cv2.WND_PROP_VISIBLE) < 1:
                # Closed with its X button: OpenCV already destroyed it.
                self._preview_open = self.preview = False
                self.refresh()
                return
            cv2.imshow(PREVIEW_WINDOW, img)
            cv2.waitKey(1)
            self._preview_open = True
        elif self._preview_open:  # turned off from the tray menu
            cv2.destroyWindow(PREVIEW_WINDOW)
            cv2.waitKey(1)
            self._preview_open = False

    def run(self):
        self.register_hotkeys()

        def setup(icon):
            icon.visible = True
            self.refresh()
            if not vcam.driver_installed():
                log.warning("%s driver is not registered", vcam.CAMERA_NAME)
                icon.notify(f"The {vcam.CAMERA_NAME} driver isn't installed, so apps can't see the camera. "
                            "Reinstall CamZoom to fix this.", APP)
            threading.Thread(target=self.video_loop, name="video", daemon=True).start()
            threading.Thread(target=self.watch_loop, name="watcher", daemon=True).start()

        self.icon.run(setup=setup)


def make_icon(state):
    """Green while the webcam is on, gray while waiting for an app, gray with a red slash when switched off."""
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    color = (46, 160, 67, 255) if state == "live" else (140, 140, 140, 255)
    draw.rounded_rectangle((2, 16, 44, 50), radius=7, fill=color)
    draw.polygon([(46, 27), (62, 18), (62, 48), (46, 39)], fill=color)
    draw.ellipse((13, 23, 33, 43), fill=(255, 255, 255, 255))
    draw.ellipse((18, 28, 28, 38), fill=color)
    if state == "off":
        draw.line((6, 6, 58, 58), fill=(255, 255, 255, 255), width=12)
        draw.line((6, 6, 58, 58), fill=(220, 50, 50, 255), width=7)
    return img


def already_running():
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    already_running.mutex = kernel32.CreateMutexW(None, False, f"Local\\{APP}SingleInstance")
    return ctypes.get_last_error() == 183  # ERROR_ALREADY_EXISTS


def setup_logging():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    handlers = [RotatingFileHandler(LOG_PATH, maxBytes=1_000_000, backupCount=1, encoding="utf-8")]
    if sys.stderr is not None:  # None under pythonw.exe
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", handlers=handlers)
    threading.excepthook = lambda a: log.error("Unhandled error in %s", a.thread.name,
                                               exc_info=(a.exc_type, a.exc_value, a.exc_traceback))


def main():
    setup_logging()
    if already_running():
        ctypes.windll.user32.MessageBoxW(None, f"{APP} is already running. Look for its icon in the system tray.",
                                         APP, 0x40)
        return
    autostart = "--autostart" in sys.argv
    log.info("Starting (autostart=%s)", autostart)
    App(autostart).run()


if __name__ == "__main__":
    main()
