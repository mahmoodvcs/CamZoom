# CamZoom

Zoom in on your webcam in Teams, Zoom, Meet or any other app — with optional face tracking that keeps
you in frame. For webcams (and apps) that have no zoom setting.

CamZoom runs in the system tray and adds a virtual webcam called **CamZoom Camera**. Pick it as your
camera in your meeting app; it shows your real webcam, cropped and zoomed.

- **Zoom** from 1× to 4× with hotkeys
- **Face tracking**: the zoomed frame smoothly follows your face
- **Brightness** like a longer exposure (black stays black, highlights roll off instead of clipping)
- **Brighten shadows**: lifts dark areas (like a face lit from behind by a window) without changing
  the bright ones
- **Turns your webcam on only while an app is using CamZoom Camera**, so the webcam light is off the
  rest of the time and your meeting app's camera button keeps working as usual
- Starts with Windows (optional)

Windows 10/11, 64-bit.

## Install

1. Download `CamZoom-<version>-setup.exe` from [Releases](../../releases) and run it.
   (It asks for administrator rights once, to install the virtual camera driver.)
2. In your meeting app's settings, choose **CamZoom Camera** as the camera.

## Use

| Keys       | Action                  |
|------------|-------------------------|
| Ctrl+Alt+= | Zoom in                 |
| Ctrl+Alt+- | Zoom out                |
| Ctrl+Alt+0 | Reset zoom              |
| Ctrl+Alt+F | Face tracking on/off    |
| Ctrl+Alt+C | Camera enabled/disabled |

Left-click the tray icon to enable/disable the camera; right-click for the menu (zoom, which webcam
to use, a preview window, settings, start with Windows).

**Settings...** opens a window with the **Brightness** and **Brighten shadows** sliders (0 = off).
Changes show up live; tick **Show preview** there to watch the effect while you adjust them.

Many webcams start out bright and then darken over a second or two as their auto-exposure settles.
If you liked the picture before it darkened, **Brightness** gets you close to it.

Tray icon: **green** = webcam on, **gray** = waiting for an app to use the camera, **red slash** =
disabled (apps see a "Camera off" card).

When an app starts using CamZoom Camera it shows "Starting camera..." for a second or two while
CamZoom opens your webcam.

Settings are stored in `%APPDATA%\CamZoom\config.json` (hotkeys can be changed there; restart
CamZoom afterwards). The log is `%APPDATA%\CamZoom\camzoom.log`.

## How it works

- The **app** (`camzoom.py`, Python) reads your webcam with OpenCV, crops and scales it, and finds
  your face with OpenCV's YuNet model. Brightness is a lookup table that multiplies the light
  (undoing the gamma curve first) and rolls off the highlights. Brighten shadows scales each pixel by how dark its
  surroundings are (an edge-preserving blur of a small copy of the frame), which takes about 2 ms per
  frame. It writes the frames into a shared-memory queue.
- The **driver** (`driver/`, C++) is a DirectShow virtual camera built from
  [OBS Studio](https://obsproject.com/)'s virtual camera. Meeting apps load it like any webcam, and
  it shows the frames from the queue. See [driver/README.md](driver/README.md) for what was changed.
- To know when an app is using the camera, CamZoom checks every 2 seconds whether any other process
  has the frame queue open (the driver holds it open exactly while streaming).

## Build from source

Needs Python 3.13+, Visual Studio 2022+ with "Desktop development with C++", and
[Inno Setup 6](https://jrsoftware.org/isinfo.php).

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
tools\build.ps1 -Version 1.0.0     # driver + app + installer -> dist\CamZoom-1.0.0-setup.exe
```

To run from source instead: `tools\build-driver.ps1`, then `tools\register-driver.ps1` (admin), then
`.venv\Scripts\python camzoom.py`.

Pushing a tag like `v1.0.0` builds the installer on GitHub Actions and attaches it to a release.

## License

GPL-2.0-or-later (see [LICENSE](LICENSE)), because the driver contains code from OBS Studio
(GPL-2.0-or-later) and libdshowcapture (LGPL-2.1-or-later).

The face detection model `models/face_detection_yunet_2023mar.onnx` is from
[OpenCV Zoo](https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet) (MIT).
