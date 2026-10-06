# CamZoom Camera: a DirectShow virtual camera built from OBS Studio's virtualcam module.
# Build once per architecture:
#   cmake -S driver -B build/x64 -A x64   && cmake --build build/x64 --config Release
#   cmake -S driver -B build/x86 -A Win32 && cmake --build build/x86 --config Release
cmake_minimum_required(VERSION 3.20)
project(camzoom-camera C CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_C_STANDARD 17)
set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")  # static CRT: no VC++ redist needed

if(CMAKE_SIZEOF_VOID_P EQUAL 8)
  set(ARCH_SUFFIX 64)
else()
  set(ARCH_SUFFIX 32)
endif()
set(OUTPUT_NAME camzoom-camera${ARCH_SUFFIX})

configure_file(camzoom-camera.def.in ${CMAKE_CURRENT_BINARY_DIR}/camzoom-camera.def)

add_library(camzoom-camera MODULE
  camzoom-camera.rc
  ${CMAKE_CURRENT_BINARY_DIR}/camzoom-camera.def
  obs/virtualcam-module.cpp
  obs/virtualcam-filter.cpp
  obs/placeholder.cpp
  obs/sleepto.c
  obs/shared-memory-queue.c
  obs/tiny-nv12-scale.c
  libdshowcapture/output-filter.cpp
  libdshowcapture/dshow-formats.cpp
  libdshowcapture/dshow-media-type.cpp
  libdshowcapture/log.cpp
  libdshowcapture/dshow-base.cpp
)

target_include_directories(camzoom-camera PRIVATE . obs libdshowcapture)
target_compile_definitions(camzoom-camera PRIVATE UNICODE _UNICODE VIRTUALCAM_AVAILABLE _CRT_SECURE_NO_WARNINGS)
target_compile_options(camzoom-camera PRIVATE /W3 /utf-8)
target_link_libraries(camzoom-camera PRIVATE strmiids gdiplus winmm)
set_target_properties(camzoom-camera PROPERTIES OUTPUT_NAME ${OUTPUT_NAME})
if(ARCH_SUFFIX EQUAL 32)
  target_link_options(camzoom-camera PRIVATE /SAFESEH:NO)
endif()
