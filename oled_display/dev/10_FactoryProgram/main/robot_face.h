#pragma once
#include <stdbool.h>

// Create the canvas, draw the default face. Call this from app_main
// while holding the LVGL lock (same as the old robot_face_init() call).
void robot_face_init(void);

// Start WiFi (blocks until connected) and spawn the Firestore poll task.
// Call this AFTER robot_face_init() and AFTER releasing the LVGL lock.
void robot_face_start_network(void);

// Thin wrappers around the LVGL mutex defined in example_qspi_with_ram.c,
// so the poll task can safely redraw the canvas.
bool robot_face_lvgl_lock(int timeout_ms);
void robot_face_lvgl_unlock(void);