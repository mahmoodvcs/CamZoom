# CamZoom Camera driver

A DirectShow virtual camera ("CamZoom Camera") that apps like Teams, Zoom and browsers can pick as a
webcam. It shows the frames the CamZoom app writes into a shared-memory queue named `CamZoomVideo`.

It is OBS Studio's virtual camera module, renamed so it can be installed alongside OBS.

## Where the code comes from

| Folder             | Source                                                                                      | License          |
|--------------------|---------------------------------------------------------------------------------------------|------------------|
| `obs/`             | [obs-studio](https://github.com/obsproject/obs-studio) @ `c5bcbca` (same as 32.2.2): `plugins/win-dshow/virtualcam-module`, `shared/obs-shared-memory-queue`, `shared/obs-tiny-nv12-scale`, `libobs/util` (`WinHandle.hpp`, `threading-windows.h`) | GPL-2.0-or-later |
| `libdshowcapture/` | [libdshowcapture](https://github.com/obsproject/libdshowcapture) @ `c13d4b7`: the output-filter subset | LGPL-2.1-or-later (see `libdshowcapture/COPYING`) |

## Changes from upstream

- New CLSID and name ("CamZoom Camera"), in `camzoom-camera.h`; queue name `CamZoomVideo`
  (`obs/shared-memory-queue.c`).
- `obs/virtualcam-filter.cpp`:
  - Removed the OBS-process detection and the `%APPDATA%\obs-virtualcam.txt` lookup; offers
    1280x720 @ 30 fps until CamZoom is running.
  - Closes the frame queue whenever the filter stops streaming. CamZoom uses "someone holds the
    queue open" to know when an app is using the camera.
  - Fixed an out-of-bounds read in `UpdatePlaceholder()` when the placeholder image has exactly the
    output size but the output format isn't NV12.
- `libdshowcapture/output-filter.cpp`: `SetFormat()` rejects pixel formats the filter doesn't offer
  (it used to accept anything, so apps could pick e.g. RGB24 and get garbage).
- `libdshowcapture/dshow-base.cpp`: trimmed to the one function the filter needs.

## Building

`tools\build-driver.ps1` (needs Visual Studio with the C++ workload) builds
`build\driver-x64\Release\camzoom-camera64.dll` and `build\driver-x86\Release\camzoom-camera32.dll`.
For development, `tools\register-driver.ps1` registers them (admin); the installer does this for users.
