/* CamZoom Camera identity. Replaces OBS's virtualcam-guid.h so CamZoom can be installed next to OBS. */
#pragma once

#include <windows.h>
#include <initguid.h>

/* {1DC4143B-5CE2-400B-9489-4D95F878366D} */
DEFINE_GUID(CLSID_OBS_VirtualVideo, 0x1dc4143b, 0x5ce2, 0x400b, 0x94, 0x89, 0x4d, 0x95, 0xf8, 0x78, 0x36, 0x6d);

#define VIRTUALCAM_NAME L"CamZoom Camera"

/* The shared-memory frame queue name ("CamZoomVideo") is set in obs/shared-memory-queue.c. */

/* Format offered before CamZoom is running; matches what CamZoom sends. */
#define VIRTUALCAM_DEFAULT_CX 1280
#define VIRTUALCAM_DEFAULT_CY 720
#define VIRTUALCAM_DEFAULT_INTERVAL 333333LL /* 100ns units: 30 fps */
