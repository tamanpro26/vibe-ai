/*
 * Template for wifi_secrets.h -- COPY this file to `wifi_secrets.h` and fill
 * in your real values. That file is gitignored and must never be committed:
 * this repository is public, so a committed password is a published password,
 * and rewriting git history afterwards does not reliably un-publish it.
 *
 *     cp wifi_secrets.example.h wifi_secrets.h
 *
 * SERVER_HOST is the LAN IP of the machine running `uvicorn api.server:app`,
 * NOT 127.0.0.1 -- the ESP32 is a separate device on the network and would
 * resolve loopback to itself. It changes whenever you switch networks, which
 * has already been the cause of several "HTTP -1 / connection refused"
 * failures; re-check it with `ipconfig` after any network change.
 */
#pragma once

#define WIFI_SSID_VALUE "YOUR_WIFI_SSID"
#define WIFI_PASSWORD_VALUE "YOUR_WIFI_PASSWORD"
#define SERVER_HOST "192.168.1.100"
