// ============================================================================
// Quartz Off-Grid Miner — Solar Demo Enclosure v1
// ----------------------------------------------------------------------------
// Layout: 2x 18650 cells in a floor cradle (along X); ESP32 LoRa board
// (Heltec V3 class, 63x28 envelope) on tall standoffs ABOVE the cells;
// wiring/charge-module bay at the +X end; OLED window in the lid;
// solar panel hovers over the lid on 4 standoff bolts (angled by spacers
// if you want tilt); SMA bulkhead + USB-C slot on the +X end wall.
//
// Print: PETG or ASA, 0.4 nozzle, 0.2 layers, 4 perimeters, 15% gyroid.
// No supports needed if printed as-is (body open-top, lid separate).
//
// Params below — measure YOUR board and adjust board_l/board_w/board_usb_y.
// ============================================================================

$fn = 64;

/* ---------------- parameters ---------------- */
wall       = 2.4;    // wall thickness (6x 0.4 nozzle)
inner_l    = 104;    // interior length  (X)
inner_w    = 54;     // interior width   (Y)
inner_h    = 40;     // interior height  (Z)

board_l    = 77;     // LilyGO T-Beam envelope (caliper-verify your rev)
board_w    = 45;
board_lift = 24;     // clears external cells + T-Beam's own under-board cell holder
board_hole_inset = 2.5;

cell_d     = 18.8;   // 18650 diameter (measure w/ wrap)
cell_l     = 68;     // 18650 length
cell_gap   = 6;      // gap between the two cells (Y)

usb_w      = 12;     // USB-C slot in end wall
usb_h      = 7;
sma_d      = 6.4;    // SMA bulkhead clearance hole

win_l      = 36;     // OLED window in lid (X) — only cut if has_display
win_w      = 22;     // (Y)
has_display = false; // T-Beam build: solid lid (true = Heltec V3 w/ screen)

screw_d    = 2.8;    // M3 self-tapping
boss_d     = 9;
lid_gap    = 0.4;    // lid rebate slack
panel_post = 14;     // solar-panel standoff height above lid

vent_n     = 6;      // vent slots per long face
vent_w     = 12;
vent_h     = 2.6;

/* ---------------- derived ---------------- */
out_l = inner_l + 2*wall;
out_w = inner_w + 2*wall;
body_h = inner_h + wall;          // body wall floor + interior
cx = out_w / 2;

// battery pack: two cells along X, centered on width, starting near -X wall
cell_y1 = cx - (cell_d + cell_gap)/2;
cell_y2 = cx + (cell_d + cell_gap)/2;
cell_x0 = wall + 2;
cradle_h = 8;                     // rail height (cells ~half buried)

// board deck above cells
board_x0 = wall + 3;
board_y0 = cx - board_w/2;

part = "all";   // "body" | "lid" | "all"

/* ---------------- helpers ---------------- */
module screw_post(h, od=9, id=2.8) {
    difference() {
        cylinder(d=od, h=h);
        translate([0,0,-0.5]) cylinder(d=id, h=h+1);
    }
}

module vent_row(z) {
    // slots spread along X on a long face
    for (i = [0 : vent_n-1]) {
        x = wall + 8 + i * (inner_l - 16 - vent_w) / (vent_n - 1);
        translate([x, -0.5, z]) cube([vent_w, wall+1, vent_h]);
    }
}

/* ---------------- case body ---------------- */
module case_body() {
    difference() {
        union() {
            // shell, open top
            difference() {
                cube([out_l, out_w, body_h]);
                translate([wall, wall, wall])
                    cube([inner_l, inner_w, body_h]);
            }
            // battery cradle rails (one channel per cell)
            for (y = [cell_y1, cell_y2])
                translate([cell_x0, y - cell_d/2 - 3, wall])
                    difference() {
                        union() {
                            cube([cell_l, cell_d + 6, cradle_h]);
                            // end stops
                            translate([-3, 0, 0]) cube([3, cell_d + 6, cradle_h + 4]);
                            translate([cell_l, 0, 0]) cube([3, cell_d + 6, cradle_h + 4]);
                        }
                        translate([-1, cell_d/2 + 3, cradle_h*0.55])
                            rotate([-90, 0, 0])
                                cylinder(d=cell_d, h=cell_d + 2);
                    }
            // zip-tie bridges across both cells (2 straps)
            for (sx = [cell_x0 + 12, cell_x0 + cell_l - 12])
                translate([sx - 2, cell_y1 - cell_d/2 - 3, wall])
                    difference() {
                        cube([4, (cell_y2 + cell_d/2 + 3) - (cell_y1 - cell_d/2 - 3), cradle_h]);
                        translate([2, 0, cradle_h - 1.2])
                            rotate([-90, 0, 0])
                                cylinder(d=3.5, h=50, $fn=24); // tie tunnel
                    }
            // board standoffs (tall, over the cells)
            for (px = [board_x0 + board_hole_inset, board_x0 + board_l - board_hole_inset],
                 py = [board_y0 + board_hole_inset, board_y0 + board_w - board_hole_inset])
                translate([px, py, wall])
                    screw_post(board_lift, od=7, id=2.8);
            // corner bosses for the lid
            for (px = [wall + boss_d/2 + 1, out_l - wall - boss_d/2 - 1],
                 py = [wall + boss_d/2 + 1, out_w - wall - boss_d/2 - 1])
                translate([px, py, wall])
                    screw_post(inner_h - 2, od=boss_d, id=screw_d);
        }
        // ---- cuts ----
        // USB-C slot in +X end wall (at board deck height)
        translate([out_l - wall - 1, board_y0 + board_w/2 - usb_w/2,
                   wall + board_lift + 2])
            cube([wall + 2, usb_w, usb_h]);
        // SMA bulkhead hole, +X end wall, upper area
        translate([out_l - wall - 1, cell_y1 - 2, wall + inner_h - 9])
            rotate([0, 90, 0]) cylinder(d=sma_d, h=wall + 2);
        // vents: both long faces, two bands
        vent_row(wall + 4);
        translate([0, out_w, 0]) vent_row(wall + 4);
        vent_row(wall + 14);
        translate([0, out_w, 0]) vent_row(wall + 14);
        // weep hole at floor low corner
        translate([out_l - 8, 8, -1]) cylinder(d=3.2, h=wall + 2);
    }
}

/* ---------------- lid ---------------- */
module lid() {
    difference() {
        union() {
            cube([out_l, out_w, wall]);
            // rebate rim into body
            translate([wall - lid_gap, wall - lid_gap, wall])
                cube([inner_l + 2*lid_gap, inner_w + 2*lid_gap, wall]);
        }
        // window over the board/OLED (board center X) — display boards only
        if (has_display)
            translate([board_x0 + board_l/2 - win_l/2, cx - win_w/2, -1])
                cube([win_l, win_w, wall*3]);
        // screw holes matching corner bosses
        for (px = [wall + boss_d/2 + 1, out_l - wall - boss_d/2 - 1],
             py = [wall + boss_d/2 + 1, out_w - wall - boss_d/2 - 1])
            translate([px, py, -1]) cylinder(d=screw_d, h=wall*3);
    }
    // solar panel standoff posts around the window
    for (px = [board_x0 + board_l/2 - 26, board_x0 + board_l/2 + 26],
         py = [cx - 21, cx + 21])
        translate([px, py, wall])
            screw_post(panel_post, od=8, id=2.8);
}

/* ---------------- assembled device view ---------------- */
tilt_extra = 8;   // rear panel posts taller => sun tilt
module device_assembly() {
    color("SlateGray", 0.95) case_body();
    // lid plate in place
    color("LightSlateGray", 0.95) translate([0, 0, body_h])
        cube([out_l, out_w, wall]);
    // panel posts (rear taller) + tilted solar slab
    post_x = [board_x0 + board_l/2 - 26, board_x0 + board_l/2 + 26];
    for (px = post_x, py = [cx - 21, cx + 21]) {
        h = (py > cx) ? panel_post + tilt_extra : panel_post;
        translate([px, py, body_h + wall]) color("Gray") cylinder(d = 8, h = h);
    }
    ang = atan(tilt_extra / 42);
    translate([post_x[0], cx - 21, body_h + wall + panel_post])
        color("MidnightBlue", 0.9) rotate([ang, 0, 0])
            cube([post_x[1] - post_x[0], 58, 3]);
    // T-Beam: PCB + under-board cell holder + modules
    translate([board_x0 + 8, cx - 9, wall + 4])
        color("DimGray") cube([52, 18, 15]);                 // holder + onboard cell
    translate([board_x0, board_y0, wall + board_lift])
        color("ForestGreen") cube([board_l, board_w, 1.6]); // PCB
    translate([board_x0 + 6, cx - 6, wall + board_lift + 1.6])
        color("Silver") cube([10, 12, 2.5]);                // GPS can
    translate([board_x0 + 24, board_y0 + 6, wall + board_lift + 1.6])
        color("Black") cube([16, 14, 2]);                   // module
    // 18650 cells in cradle
    for (y = [cell_y1, cell_y2])
        translate([cell_x0 + cell_l/2, y, wall + cradle_h*0.55])
            rotate([0, 90, 0]) color("Teal") cylinder(d = cell_d, h = cell_l, center = true);
    // antenna: SMA base + whip
    translate([out_l - 2, cell_y1 - 2, wall + inner_h - 9])
        color("Gold") cylinder(d = 8, h = 5);
    translate([out_l - 2, cell_y1 - 2, wall + inner_h - 4])
        color("Black") cylinder(d1 = 4.5, d2 = 2, h = 42);
}

/* ---------------- assembly / parts ---------------- */
if (part == "body") {
    case_body();
} else if (part == "lid") {
    translate([0, 0, body_h + 16]) lid();   // float for standalone print
} else if (part == "assembly") {
    device_assembly();
} else {
    case_body();
    translate([0, 0, body_h + 12]) lid();   // exploded view
}
