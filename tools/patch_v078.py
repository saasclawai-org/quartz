#!/usr/bin/env python3
"""v078 patch set — both builds. Idempotent, anchored, asserted.

Scope: version bump, mesh two-clock fix, HELLO height,
mining-console wifi/node commands, seed-confirm repeating banner.
Deferred: A2 recover, A4 seed enforcement, A6 board names.

Already applied on 2026-08-29 (skipped via markers):
  firmware/main/quartz.h, quartz_mesh.c, quartz_mesh.h
"""
import io
import os

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # quartz/
FW = os.path.join(BASE, 'firmware')        # classic ESP32 (LilyGO T3)
C3 = os.path.join(BASE, 'firmware-c3')     # ESP32-C3 (Super Mini)


def rd(p):
    return io.open(p, encoding='utf-8').read()


def wr(p, s):
    io.open(p, 'w', encoding='utf-8').write(s)


def patch(path, pairs, skip_marker=None):
    src = rd(path)
    if skip_marker and skip_marker in src:
        print('SKIP (already patched):', os.path.relpath(path, BASE))
        return
    for old, new in pairs:
        n = src.count(old)
        assert n == 1, 'ANCHOR x%d in %s: %r' % (n, os.path.relpath(path, BASE), old[:70])
        src = src.replace(old, new)
    wr(path, src)
    print('patched:', os.path.relpath(path, BASE))


# ---------------------------------------------------------------- shared --
VERSION = (
    '#define FW_VERSION_STRING      "v077"',
    '#define FW_VERSION_STRING      "v078"',
)

CLOCK_FIX = (
    'void quartz_mesh_step(uint32_t uptime_sec) {\n    if (!s_initialized) return;\n',
    'void quartz_mesh_step(uint32_t uptime_sec) {\n    if (!s_initialized) return;\n\n'
    '    /* v078: single time base — last_seen is stamped from the tick counter\n'
    '     * in on_recv; mixing it with the caller\'s esp_timer-derived uptime\n'
    '     * caused spurious peer timeouts (see V078-FLEET-PLAN.md A5). */\n'
    '    uptime_sec = xTaskGetTickCount() / pdMS_TO_TICKS(1000);\n',
)

STATIC_CAPS = (
    'static uint8_t s_caps = 0;\n',
    'static uint8_t s_caps = 0;\n'
    'static uint32_t s_mesh_height = 0;   /* v078: chain height for HELLO */\n',
)

HELLO_HEIGHT = (
    '    /* height left as 0 for now — main.c can update via update_caps */\n',
    '    hello.height = s_mesh_height;   /* v078: real chain height in beacons */\n',
)

CAPS_SETTER = (
    'void quartz_mesh_update_caps(uint8_t caps) {\n    s_caps = caps;\n    if (s_initialized) send_hello();\n}\n',
    'void quartz_mesh_update_caps(uint8_t caps) {\n    s_caps = caps;\n    if (s_initialized) send_hello();\n}\n\n'
    'void quartz_mesh_update_height(uint32_t height) {\n    s_mesh_height = height;\n}\n',
)

MESH_H_DECL = (
    'void quartz_mesh_update_caps(uint8_t caps);\n',
    'void quartz_mesh_update_caps(uint8_t caps);\n\n'
    '/**\n * Update our chain height (announced in HELLO beacons).\n */\n'
    'void quartz_mesh_update_height(uint32_t height);\n',
)

GOTWORK = (
    '                ESP_LOGI(TAG, "📡 Got work: block %d, target %d",\n'
    '                         tmpl.height, tmpl.target_bits);\n',
    '                ESP_LOGI(TAG, "📡 Got work: block %d, target %d",\n'
    '                         tmpl.height, tmpl.target_bits);\n'
    '                quartz_mesh_update_height(tmpl.height);   /* v078 */\n',
)

BANNER_DECL = (
    '            int boot_hold_ms = 0;',
    '            int boot_hold_ms = 0;\n'
    '            int confirm_wait_ms = 0;   /* v078: repeating banner timer */',
)

BANNER_TAIL_OLD = (
    '                } else {\n                    boot_hold_ms = 0;\n                }\n\n'
    '                vTaskDelay(pdMS_TO_TICKS(50));'
)
BANNER_TAIL_NEW = (
    '                } else {\n                    boot_hold_ms = 0;\n                }\n\n'
    '                /* v078: unmissable repeating banner while unconfirmed */\n'
    '                confirm_wait_ms += 50;\n'
    '                if (confirm_wait_ms >= 10000) {\n'
    '                    confirm_wait_ms = 0;\n'
    '                    ESP_LOGW(TAG, "⏳ WALLET NOT CONFIRMED — MINING WILL NOT START");\n'
    '                    ESP_LOGW(TAG, "   → type \'confirm\' + Enter   (or hold BOOT/PRG 3s)");\n'
    '                }\n\n'
    '                vTaskDelay(pdMS_TO_TICKS(50));'
)


def console_branches(var, ind):
    """wifi + node console branches, parametrized by buffer-var name and indent."""
    i0 = ind
    i1 = ind + '    '
    lines = [
        i0 + '} else if (strcasecmp(' + var + ', "wifi") == 0) {',
        i1 + '/* v078: wipe WiFi + node settings, reboot into portal */',
        i1 + 'nvs_handle_t h;',
        i1 + 'if (nvs_open("qz_wifi", NVS_READWRITE, &h) == ESP_OK) {',
        i1 + '    nvs_erase_key(h, "ssid");',
        i1 + '    nvs_erase_key(h, "pass");',
        i1 + '    nvs_erase_key(h, "node");',
        i1 + '    nvs_commit(h);',
        i1 + '    nvs_close(h);',
        i1 + '}',
        i1 + 'ESP_LOGI(TAG, "WiFi cleared — rebooting into portal (Quartz-XXXX AP)");',
        i1 + 'vTaskDelay(pdMS_TO_TICKS(500));',
        i1 + 'esp_restart();',
        i0 + '} else if (strncasecmp(' + var + ', "node ", 5) == 0) {',
        i1 + '/* v078: set node endpoint without wiping anything */',
        i1 + 'const char *spec = ' + var + ' + 5;',
        i1 + 'size_t slen = strlen(spec);',
        i1 + 'bool ok = (slen >= 7 && slen < 64);',
        i1 + 'for (size_t i = 0; ok && i < slen; i++) {',
        i1 + '    char cc = spec[i];',
        i1 + '    if (!((cc >= \'a\' && cc <= \'z\') || (cc >= \'A\' && cc <= \'Z\') ||',
        i1 + '          (cc >= \'0\' && cc <= \'9\') ||',
        i1 + '          cc == \'.\' || cc == \':\' || cc == \'-\' || cc == \'/\'))',
        i1 + '        ok = false;',
        i1 + '}',
        i1 + 'if (ok) {',
        i1 + '    nvs_handle_t h;',
        i1 + '    if (nvs_open("qz_wifi", NVS_READWRITE, &h) == ESP_OK) {',
        i1 + '        nvs_set_str(h, "node", spec);',
        i1 + '        nvs_commit(h);',
        i1 + '        nvs_close(h);',
        i1 + '        ESP_LOGI(TAG, "Node endpoint set to %s — rebooting…", spec);',
        i1 + '        vTaskDelay(pdMS_TO_TICKS(500));',
        i1 + '        esp_restart();',
        i1 + '    }',
        i1 + '} else {',
        i1 + '    ESP_LOGW(TAG, "Usage: node <host[:port]>  (e.g. node 192.168.1.142 or node quartzchain.net)");',
        i1 + '}',
        i0 + '} else if (strcasecmp(' + var + ', "node") == 0) {',
        i1 + 'ESP_LOGI(TAG, "Node endpoint: %s:%d", quartz_wifi_node_host(), quartz_wifi_node_port());',
    ]
    return '\n'.join(lines) + '\n'


# ------------------------------------------------- classic (firmware/) ----
HELP_ANCHOR = '    } else if (strcasecmp(cmd, "help") == 0) {\n'
PIN_HELP = ('        ESP_LOGI(TAG, "  pinstatus            PIN state");\n    }\n}',
            '        ESP_LOGI(TAG, "  pinstatus            PIN state");\n'
            '        ESP_LOGI(TAG, "  node [host[:port]]   show/set node endpoint");\n'
            '        ESP_LOGI(TAG, "  wifi                 wipe WiFi + node, reboot to portal");\n'
            '    }\n}')

patch(os.path.join(FW, 'main', 'quartz.h'), [VERSION], skip_marker='v078')
patch(os.path.join(FW, 'main', 'quartz_mesh.c'),
      [CLOCK_FIX, STATIC_CAPS, HELLO_HEIGHT, CAPS_SETTER],
      skip_marker='s_mesh_height')
patch(os.path.join(FW, 'main', 'quartz_mesh.h'), [MESH_H_DECL],
      skip_marker='update_height')
patch(os.path.join(FW, 'main', 'main.c'),
      [GOTWORK,
       (HELP_ANCHOR, console_branches('cmd', '    ') + HELP_ANCHOR),
       PIN_HELP,
       BANNER_DECL,
       (BANNER_TAIL_OLD, BANNER_TAIL_NEW)],
      skip_marker='WALLET NOT CONFIRMED')

# ------------------------------------------------------- C3 (firmware-c3) --
C3_CONSOLE_OLD = (
    '            } else {\n'
    '                ESP_LOGW(TAG, "Usage: meshch <1-13>  (meshch 0 = auto-discover)");\n'
    '            }\n'
    '        }\n'
    'console_next:'
)
C3_CONSOLE_NEW = (
    '            } else {\n'
    '                ESP_LOGW(TAG, "Usage: meshch <1-13>  (meshch 0 = auto-discover)");\n'
    '            }\n'
    + console_branches('line', '        ')
    + '        }\n'
    'console_next:'
)

patch(os.path.join(C3, 'main', 'quartz.h'), [VERSION], skip_marker='v078')
patch(os.path.join(C3, 'main', 'quartz_mesh.c'),
      [CLOCK_FIX, STATIC_CAPS, HELLO_HEIGHT, CAPS_SETTER],
      skip_marker='s_mesh_height')
patch(os.path.join(C3, 'main', 'quartz_mesh.h'), [MESH_H_DECL],
      skip_marker='update_height')
patch(os.path.join(C3, 'main', 'main.c'),
      [GOTWORK, (C3_CONSOLE_OLD, C3_CONSOLE_NEW), BANNER_DECL,
       (BANNER_TAIL_OLD, BANNER_TAIL_NEW)],
      skip_marker='WALLET NOT CONFIRMED')

print('\nAll patches applied.')
