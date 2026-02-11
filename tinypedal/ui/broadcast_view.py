#  TinyPedal is an open-source overlay application for racing simulation.
#  Copyright (C) 2022-2026 TinyPedal developers, see contributors.md file
#
#  This file is part of TinyPedal.
#
#  This program is free software: you can redistribute it and/or modify
#  it under the terms of the GNU General Public License as published by
#  the Free Software Foundation, either version 3 of the License, or
#  (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""
Broadcast list view
"""

import logging
from time import monotonic

from PySide2.QtCore import Qt, Slot, QTimer
from PySide2.QtGui import QColor, QFont
from PySide2.QtWidgets import (
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .. import app_signal
from ..api_control import api
from ..calculation import sec2laptime
from ..const_common import MAX_SECONDS
from ..module_info import minfo
from ..setting import cfg
from ._common import UIScaler

logger = logging.getLogger(__name__)


SORT_STANDINGS = 0
SORT_RELATIVE = 1
SORT_LABELS = ("Standings", "Relative")
BATTLE_THRESHOLD = 1.0  # seconds
CLOSE_THRESHOLD = 2.0  # seconds
LAPPING_THRESHOLD = 3.0  # seconds proximity to blue-flagged car
YELLOW_SPEED_THRESHOLD = 8  # m/s
YELLOW_STICKY_DURATION = 5.0  # seconds to keep yellow highlight after clearing
COLOR_BATTLE = QColor(34, 139, 34)  # green
COLOR_CLOSE = QColor(255, 140, 0)  # orange
COLOR_YELLOW = QColor(255, 255, 0)  # yellow
COLOR_BLUE = QColor(0, 160, 255)  # blue
COLOR_PENALTY = QColor(220, 30, 30)  # red
COLOR_PIT = QColor(150, 150, 150)  # grey


class BroadcastList(QWidget):
    """Broadcast list view"""

    def __init__(self, parent):
        super().__init__(parent)
        self.last_enabled = None
        self._sort_mode = SORT_STANDINGS
        self._yellow_timestamps = {}  # driver_index -> last time yellow was active
        self._top_speeds = {}  # driver_index -> max speed in m/s

        # Label
        self.label_spectating = QLabel("")

        # List box
        self.listbox_spectate = QListWidget(self)
        self.listbox_spectate.setAlternatingRowColors(True)
        self.listbox_spectate.itemDoubleClicked.connect(self.spectate_selected)
        driver_font = QFont("Consolas", 10)
        self.listbox_spectate.setFont(driver_font)
        self.listbox_spectate.setStyleSheet(
            "QListWidget { border: 1px solid #555; border-radius: 3px; }"
            "QListWidget::item { padding: 2px 4px; }"
            "QListWidget::item:selected { background: #2980b9; color: white; }"
        )

        # Button
        self.button_spectate = QPushButton("Spectate")
        self.button_spectate.clicked.connect(self.spectate_selected)

        self.button_focus = QPushButton("Focus Camera")
        self.button_focus.clicked.connect(self.focus_camera)

        self.button_refresh = QPushButton("Refresh")
        self.button_refresh.clicked.connect(self.refresh)

        self.button_toggle = QPushButton("")
        self.button_toggle.setCheckable(True)
        self.button_toggle.toggled.connect(self.toggle_spectate)

        self.button_sort = QPushButton(SORT_LABELS[SORT_STANDINGS])
        self.button_sort.clicked.connect(self._toggle_sort_mode)

        layout_button = QHBoxLayout()
        layout_button.addWidget(self.button_spectate)
        layout_button.addWidget(self.button_focus)
        layout_button.addWidget(self.button_refresh)
        layout_button.addWidget(self.button_sort)
        layout_button.addStretch(1)
        layout_button.addWidget(self.button_toggle)

        # Timing info panel (compact)
        self.timing_group = QGroupBox("Live Timing")
        small_font = QFont()
        small_font.setPointSize(8)
        self.timing_group.setFont(small_font)
        timing_grid = QGridLayout()
        timing_grid.setSpacing(1)
        timing_grid.setContentsMargins(4, 4, 4, 4)

        # Row 0: Current lap / Laps
        self.label_current_lap = QLabel("--")
        self.label_laps = QLabel("--")
        timing_grid.addWidget(QLabel("Current"), 0, 0)
        timing_grid.addWidget(self.label_current_lap, 0, 1)
        timing_grid.addWidget(QLabel("Laps"), 0, 2)
        timing_grid.addWidget(self.label_laps, 0, 3)

        # Row 1: Best / Last
        self.label_best_lap = QLabel("--")
        self.label_last_lap = QLabel("--")
        timing_grid.addWidget(QLabel("Best"), 1, 0)
        timing_grid.addWidget(self.label_best_lap, 1, 1)
        timing_grid.addWidget(QLabel("Last"), 1, 2)
        timing_grid.addWidget(self.label_last_lap, 1, 3)

        # Row 2: Sectors
        self.label_sector1 = QLabel("--")
        self.label_sector2 = QLabel("--")
        self.label_sector3 = QLabel("--")
        timing_grid.addWidget(QLabel("S1"), 2, 0)
        timing_grid.addWidget(self.label_sector1, 2, 1)
        timing_grid.addWidget(QLabel("S2"), 2, 2)
        timing_grid.addWidget(self.label_sector2, 2, 3)
        timing_grid.addWidget(QLabel("S3"), 2, 4)
        timing_grid.addWidget(self.label_sector3, 2, 5)

        # Row 3: Best sectors
        self.label_best_s1 = QLabel("--")
        self.label_best_s2 = QLabel("--")
        self.label_best_s3 = QLabel("--")
        timing_grid.addWidget(QLabel("BS1"), 3, 0)
        timing_grid.addWidget(self.label_best_s1, 3, 1)
        timing_grid.addWidget(QLabel("BS2"), 3, 2)
        timing_grid.addWidget(self.label_best_s2, 3, 3)
        timing_grid.addWidget(QLabel("BS3"), 3, 4)
        timing_grid.addWidget(self.label_best_s3, 3, 5)

        # Row 4: Gaps
        self.label_gap_leader = QLabel("--")
        self.label_gap_next = QLabel("--")
        timing_grid.addWidget(QLabel("Leader"), 4, 0)
        timing_grid.addWidget(self.label_gap_leader, 4, 1)
        timing_grid.addWidget(QLabel("Next"), 4, 2)
        timing_grid.addWidget(self.label_gap_next, 4, 3)

        # Row 5: Speed / Top Speed
        self.label_speed = QLabel("--")
        self.label_top_speed = QLabel("--")
        timing_grid.addWidget(QLabel("Speed"), 5, 0)
        timing_grid.addWidget(self.label_speed, 5, 1)
        timing_grid.addWidget(QLabel("Top"), 5, 2)
        timing_grid.addWidget(self.label_top_speed, 5, 3)

        # Row 6: Penalty
        self.label_penalty = QLabel("--")
        timing_grid.addWidget(QLabel("Penalty"), 6, 0)
        timing_grid.addWidget(self.label_penalty, 6, 1, 1, 3)

        self.timing_group.setLayout(timing_grid)

        # Damage info panel (compact)
        self.damage_group = QGroupBox("Damage")
        self.damage_group.setFont(small_font)
        damage_layout = QVBoxLayout()
        damage_layout.setSpacing(1)
        damage_layout.setContentsMargins(4, 4, 4, 4)

        self.label_integrity = QLabel("Integrity: --")
        self.bar_integrity = QProgressBar()
        self.bar_integrity.setRange(0, 100)
        self.bar_integrity.setValue(100)
        self.bar_integrity.setTextVisible(False)
        self.bar_integrity.setFixedHeight(6)
        self.bar_integrity.setStyleSheet(
            "QProgressBar { background: #444; border: none; border-radius: 3px; }"
            "QProgressBar::chunk { background: #2ecc71; border-radius: 3px; }"
        )

        self.label_body = QLabel("Body: OK")
        self.label_aero = QLabel("Aero: OK")
        self.label_susp = QLabel("Susp: OK")
        self.label_detached = QLabel("")

        damage_layout.addWidget(self.label_integrity)
        damage_layout.addWidget(self.bar_integrity)
        damage_layout.addWidget(self.label_body)
        damage_layout.addWidget(self.label_aero)
        damage_layout.addWidget(self.label_susp)
        damage_layout.addWidget(self.label_detached)
        damage_layout.addStretch(1)
        self.damage_group.setLayout(damage_layout)

        # Virtual energy bar (compact)
        self.energy_group = QGroupBox("Virtual Energy")
        self.energy_group.setFont(small_font)
        energy_layout = QVBoxLayout()
        energy_layout.setSpacing(2)
        energy_layout.setContentsMargins(4, 4, 4, 4)

        self.label_energy = QLabel("Energy: --")
        self.bar_energy = QProgressBar()
        self.bar_energy.setRange(0, 1000)
        self.bar_energy.setValue(0)
        self.bar_energy.setTextVisible(False)
        self.bar_energy.setFixedHeight(14)
        self.bar_energy.setStyleSheet(
            "QProgressBar { background: #333; border: 1px solid #555; border-radius: 4px; }"
            "QProgressBar::chunk { background: qlineargradient("
            "x1:0, y1:0, x2:1, y2:0, stop:0 #2980b9, stop:1 #3498db); border-radius: 3px; }"
        )

        energy_layout.addWidget(self.label_energy)
        energy_layout.addWidget(self.bar_energy)
        self.energy_group.setLayout(energy_layout)

        # Bottom panel: timing left, damage right
        bottom_layout = QHBoxLayout()
        bottom_layout.addWidget(self.timing_group, 3)
        bottom_layout.addWidget(self.damage_group, 1)

        # Layout
        layout_main = QVBoxLayout()
        layout_main.addWidget(self.label_spectating)
        layout_main.addWidget(self.listbox_spectate, 1)  # stretch priority
        layout_main.addLayout(layout_button)
        layout_main.addLayout(bottom_layout)
        layout_main.addWidget(self.energy_group)
        margin = UIScaler.pixel(6)
        layout_main.setContentsMargins(margin, margin, margin, margin)
        self.setLayout(layout_main)

        # Live timing update timer
        self._timing_timer = QTimer(self)
        self._timing_timer.timeout.connect(self._update_timing)
        self._timing_timer.setInterval(100)
        self._list_update_counter = 0

    @Slot(bool)  # type: ignore[operator]
    def refresh(self):
        """Refresh spectate list"""
        enabled = cfg.api["enable_player_index_override"]

        if enabled:
            self.update_drivers("Anonymous", cfg.api["player_index"], False)
        else:
            self.listbox_spectate.clear()
            self.label_spectating.setText("Spectating: <b>Disabled</b>")

        # Update button state only if changed
        if self.last_enabled != enabled:
            self.last_enabled = enabled
            self.set_enable_state(enabled)

    def set_enable_state(self, enabled: bool):
        """Set enable state"""
        self.button_toggle.setChecked(enabled)
        self.button_toggle.setText("Enabled" if enabled else "Disabled")
        self.listbox_spectate.setDisabled(not enabled)
        self.button_spectate.setDisabled(not enabled)
        self.button_focus.setDisabled(not enabled)
        self.button_refresh.setDisabled(not enabled)
        self.button_sort.setDisabled(not enabled)
        self.label_spectating.setDisabled(not enabled)
        self.timing_group.setDisabled(not enabled)
        self.damage_group.setDisabled(not enabled)
        self.energy_group.setDisabled(not enabled)
        if enabled:
            logger.info("ENABLED: broadcast mode")
            self._list_update_counter = 9  # first auto-refresh on next tick
            self._timing_timer.start()
            self.refresh()  # force immediate list build
        else:
            logger.info("DISABLED: broadcast mode")
            self._timing_timer.stop()
            self._reset_timing()

    def toggle_spectate(self, checked: bool):
        """Toggle spectate mode"""
        cfg.api["enable_player_index_override"] = checked
        cfg.save()
        api.setup()
        app_signal.refresh.emit(True)

    def spectate_selected(self):
        """Spectate selected player"""
        self.update_drivers(self.selected_name(), -1, True)
        self.focus_camera()

    def focus_camera(self):
        """Switch in-game camera to the currently spectated driver"""
        index = cfg.api["player_index"]
        if index < 0 or not cfg.api["enable_player_index_override"]:
            return
        try:
            total = api.read.vehicle.total_vehicles()
            if index >= total:
                return
            slot_id = api.read.vehicle.slot_id(index)
            api.watch_vehicle(slot_id)
        except (AttributeError, IndexError):
            pass

    def update_drivers(self, selected_driver_name: str, selected_index: int, match_name: bool):
        """Update drivers list"""
        listbox = self.listbox_spectate
        driver_list = []

        # Gather relative info for relative sort mode
        laptime_est = api.read.timing.estimated_laptime()
        plr_time = api.read.timing.estimated_time_into(selected_index) if selected_index >= 0 else 0

        for driver_index in range(api.read.vehicle.total_vehicles()):
            driver_name = api.read.vehicle.driver_name(driver_index)
            driver_place = api.read.vehicle.place(driver_index)
            driver_class = api.read.vehicle.class_name(driver_index)
            in_pits = api.read.vehicle.in_pits(driver_index) or api.read.vehicle.in_garage(driver_index)
            is_yellow = self._check_yellow(driver_index, in_pits)
            is_blue = api.read.session.blue_flag(driver_index)
            if driver_index == selected_index or laptime_est <= 0 or selected_index < 0:
                rel_gap = 0.0
            else:
                opt_time = api.read.timing.estimated_time_into(driver_index)
                diff = opt_time - plr_time
                diff = diff - diff // laptime_est * laptime_est
                # Normalize to range (-half_lap, +half_lap]
                if diff > laptime_est * 0.5:
                    diff -= laptime_est
                rel_gap = diff

            driver_list.append((driver_place, driver_class, driver_name, driver_index, rel_gap, in_pits, is_yellow, is_blue))
            if match_name:
                if driver_name == selected_driver_name:
                    selected_index = driver_index
            else:  # match index
                if driver_index == selected_index:
                    selected_driver_name = driver_name

        # Calculate position in class and detect battles
        class_positions = self._calc_class_positions(driver_list)
        battles, close = self._find_battles(driver_list, laptime_est)

        self._populate_listbox(listbox, driver_list, class_positions, battles, close, laptime_est)
        self.focus_on_selected(selected_driver_name)
        self.save_selected_index(selected_index)

    def focus_on_selected(self, driver_name: str):
        """Focus on selected driver row"""
        listbox = self.listbox_spectate
        for row_index in range(listbox.count()):
            if driver_name == listbox.item(row_index).data(Qt.UserRole):
                break
        else:  # fallback to 0 if name not found
            row_index = 0
        listbox.setCurrentRow(row_index)
        # Make sure selected name valid
        self.label_spectating.setText(f"Spectating: <b>{self.selected_name()}</b>")

    def selected_name(self) -> str:
        """Selected driver name"""
        selected_item = self.listbox_spectate.currentItem()
        if selected_item is None:
            return "Anonymous"
        return selected_item.data(Qt.UserRole) or "Anonymous"

    def _refresh_list_only(self):
        """Refresh the driver list without saving selection (for auto-update)"""
        selected_index = cfg.api["player_index"]

        listbox = self.listbox_spectate
        driver_list = []

        laptime_est = api.read.timing.estimated_laptime()
        plr_time = api.read.timing.estimated_time_into(selected_index) if selected_index >= 0 else 0
        selected_driver_name = "Anonymous"

        for driver_index in range(api.read.vehicle.total_vehicles()):
            driver_name = api.read.vehicle.driver_name(driver_index)
            driver_place = api.read.vehicle.place(driver_index)
            driver_class = api.read.vehicle.class_name(driver_index)
            in_pits = api.read.vehicle.in_pits(driver_index) or api.read.vehicle.in_garage(driver_index)
            is_yellow = self._check_yellow(driver_index, in_pits)
            is_blue = api.read.session.blue_flag(driver_index)

            if driver_index == selected_index or laptime_est <= 0:
                rel_gap = 0.0
            else:
                opt_time = api.read.timing.estimated_time_into(driver_index)
                diff = opt_time - plr_time
                diff = diff - diff // laptime_est * laptime_est
                if diff > laptime_est * 0.5:
                    diff -= laptime_est
                rel_gap = diff

            driver_list.append((driver_place, driver_class, driver_name, driver_index, rel_gap, in_pits, is_yellow, is_blue))
            if driver_index == selected_index:
                selected_driver_name = driver_name

        class_positions = self._calc_class_positions(driver_list)
        battles, close = self._find_battles(driver_list, laptime_est)

        self._populate_listbox(listbox, driver_list, class_positions, battles, close, laptime_est)
        self.focus_on_selected(selected_driver_name)

    def _populate_listbox(self, listbox, driver_list, class_positions, battles, close, laptime_est):
        """Populate driver list grouped by class with separators"""
        listbox.clear()

        anon_item = QListWidgetItem("Anonymous")
        anon_item.setData(Qt.UserRole, "Anonymous")
        listbox.addItem(anon_item)

        # Find cars involved in lapping (near a blue-flagged car)
        lapping = self._find_lappers(driver_list, laptime_est)

        # Group drivers by class
        class_groups = {}
        for entry in driver_list:
            cls = entry[1]  # class_name
            class_groups.setdefault(cls, []).append(entry)

        # Sort each class group internally by class position
        for cls in class_groups:
            class_groups[cls].sort(key=lambda x: class_positions.get(x[3], x[0]))

        # Order classes by best overall position (leader of each class)
        sorted_classes = sorted(
            class_groups.keys(),
            key=lambda c: min(e[0] for e in class_groups[c])
        )

        first_class = True
        for cls in sorted_classes:
            # Add separator between classes
            if not first_class:
                sep = QListWidgetItem("")
                sep.setFlags(Qt.NoItemFlags)
                listbox.addItem(sep)
            # Class header
            header = QListWidgetItem(f"--- {cls} ---")
            header.setFlags(Qt.NoItemFlags)
            header.setForeground(QColor("#888888"))
            listbox.addItem(header)
            first_class = False

            for place, class_name, name, _index, rel_gap, in_pits, is_yellow, is_blue in class_groups[cls]:
                class_pos = class_positions.get(_index, place)
                ve_str = self._format_ve(_index)
                penalty_tag = self._get_penalty_tag(_index)
                is_lapping = _index in lapping
                tags = ""
                if penalty_tag:
                    tags += f" {penalty_tag}"
                if in_pits:
                    tags += " PIT"
                if is_yellow:
                    tags += " YEL"
                if is_blue:
                    tags += " BLU"
                if not is_yellow and not is_blue and not is_lapping and _index in battles:
                    tags += " BTL"
                padded_name = name[:20].ljust(20)
                if self._sort_mode == SORT_RELATIVE:
                    gap_str = f"+{rel_gap:.1f}" if rel_gap >= 0 else f"{rel_gap:.1f}"
                    display_text = f"{gap_str:>6s} P{class_pos:<2d} {padded_name} {ve_str}{tags}"
                else:
                    display_text = f"P{class_pos:<2d} {padded_name} {ve_str}{tags}"
                item = QListWidgetItem(display_text)
                item.setData(Qt.UserRole, name)
                has_penalty = penalty_tag != ""
                if has_penalty:
                    item.setForeground(COLOR_PENALTY)
                elif is_yellow:
                    item.setForeground(COLOR_YELLOW)
                elif is_blue:
                    item.setForeground(COLOR_BLUE)
                elif in_pits:
                    item.setForeground(COLOR_PIT)
                elif not is_lapping and _index in battles:
                    item.setForeground(COLOR_BATTLE)
                elif not is_lapping and _index in close:
                    item.setForeground(COLOR_CLOSE)
                listbox.addItem(item)

    @staticmethod
    def save_selected_index(index: int):
        """Save selected driver index"""
        if cfg.api["player_index"] != index:
            cfg.api["player_index"] = index
            api.setup()
            cfg.save()
        if cfg.api["enable_player_index_override"] and index >= 0:
            api.watch_vehicle(api.read.vehicle.slot_id(index))

    def _toggle_sort_mode(self):
        """Toggle between standings and relative sort order"""
        self._sort_mode = SORT_RELATIVE if self._sort_mode == SORT_STANDINGS else SORT_STANDINGS
        self.button_sort.setText(SORT_LABELS[self._sort_mode])
        if cfg.api["enable_player_index_override"]:
            self.update_drivers(self.selected_name(), cfg.api["player_index"], False)

    def _check_yellow(self, driver_index: int, in_pits: bool) -> bool:
        """Check yellow flag with sticky duration

        Returns True if the driver is currently slow on track,
        or was slow within the last YELLOW_STICKY_DURATION seconds.
        """
        if in_pits:
            self._yellow_timestamps.pop(driver_index, None)
            return False
        now = monotonic()
        if api.read.vehicle.speed(driver_index) < YELLOW_SPEED_THRESHOLD:
            self._yellow_timestamps[driver_index] = now
            return True
        last_yellow = self._yellow_timestamps.get(driver_index)
        if last_yellow is not None and now - last_yellow < YELLOW_STICKY_DURATION:
            return True
        self._yellow_timestamps.pop(driver_index, None)
        return False

    @staticmethod
    def _calc_class_positions(driver_list):
        """Calculate position in class for each driver

        Args:
            driver_list: list of (place, class_name, name, index, rel_gap).

        Returns:
            dict mapping driver index to position in class.
        """
        # Group by class, sorted by overall position within each class
        classes = {}
        for place, class_name, _name, driver_index, *_ in driver_list:
            classes.setdefault(class_name, []).append((place, driver_index))

        class_positions = {}
        for entries in classes.values():
            entries.sort()  # sort by overall place
            for pos, (_, driver_index) in enumerate(entries, 1):
                class_positions[driver_index] = pos
        return class_positions

    @staticmethod
    def _find_battles(driver_list, laptime_est):
        """Find drivers within proximity thresholds of a same-class car on track

        Drivers in pits are excluded.

        Returns:
            tuple of (battles, close) sets of driver indices.
            battles: within BATTLE_THRESHOLD (< 1s).
            close: within CLOSE_THRESHOLD (< 2s) but not in battles.
        """
        battles = set()
        close = set()
        if laptime_est <= 0:
            return battles, close

        # Group on-track drivers by class (exclude pitting, yellow, blue flagged)
        classes = {}
        for _place, cls, _name, idx, _gap, in_pits, is_yellow, is_blue in driver_list:
            if not in_pits and not is_yellow and not is_blue:
                classes.setdefault(cls, []).append(idx)

        half_lap = laptime_est * 0.5

        for indices in classes.values():
            if len(indices) < 2:
                continue
            # Collect estimated time into lap for each driver in class
            time_into = {idx: api.read.timing.estimated_time_into(idx) for idx in indices}
            for i in range(len(indices)):
                for j in range(i + 1, len(indices)):
                    diff = time_into[indices[j]] - time_into[indices[i]]
                    diff = diff - diff // laptime_est * laptime_est
                    if diff > half_lap:
                        diff -= laptime_est
                    gap = abs(diff)
                    if gap <= BATTLE_THRESHOLD:
                        battles.add(indices[i])
                        battles.add(indices[j])
                    elif gap <= CLOSE_THRESHOLD:
                        close.add(indices[i])
                        close.add(indices[j])
        # Remove from close any that are already in battles
        close -= battles
        return battles, close

    @staticmethod
    def _find_lappers(driver_list, laptime_est):
        """Find non-blue drivers near a blue-flagged car (the lapper)

        Returns:
            set of driver indices that are lapping a blue-flagged car.
        """
        lapping = set()
        if laptime_est <= 0:
            return lapping

        blue_drivers = []
        non_blue_drivers = []
        for _place, _cls, _name, idx, _gap, in_pits, _is_yellow, is_blue in driver_list:
            if in_pits:
                continue
            if is_blue:
                blue_drivers.append(idx)
            else:
                non_blue_drivers.append(idx)

        if not blue_drivers:
            return lapping

        half_lap = laptime_est * 0.5
        # Cache time-into-lap for relevant drivers
        time_into = {}
        for idx in blue_drivers:
            time_into[idx] = api.read.timing.estimated_time_into(idx)
        for idx in non_blue_drivers:
            time_into[idx] = api.read.timing.estimated_time_into(idx)

        for b_idx in blue_drivers:
            for nb_idx in non_blue_drivers:
                diff = time_into[nb_idx] - time_into[b_idx]
                diff = diff - diff // laptime_est * laptime_est
                if diff > half_lap:
                    diff -= laptime_est
                if abs(diff) <= LAPPING_THRESHOLD:
                    lapping.add(nb_idx)

        return lapping

    @staticmethod
    def _format_time(seconds: float) -> str:
        """Format time value for display"""
        if seconds <= 0 or seconds >= MAX_SECONDS:
            return "-:--.---"
        return sec2laptime(seconds)

    @staticmethod
    def _format_ve(driver_index: int) -> str:
        """Format virtual energy remaining for driver list"""
        try:
            veh = minfo.vehicles.dataSet[driver_index]
            if not veh.driverName:
                return "               "
            ve = veh.energyRemaining
            if ve <= -1.0:
                return "               "
            pct = max(0, min(100, int(ve * 100)))
            filled = pct // 10
            bar = "|" * filled + "." * (10 - filled)
            return f"[{bar}]{pct:3d}%"
        except (AttributeError, IndexError):
            return "               "

    @staticmethod
    def _get_penalty_tag(driver_index: int) -> str:
        """Get penalty tag for driver (DT, SG, etc)
        
        Note: mCountLapFlag indicates lap counting behavior during penalty,
        not necessarily the penalty type. This is a best-effort detection.
        """
        try:
            penalties = api.read.vehicle.number_penalties(driver_index)
            if penalties <= 0:
                return ""
            # Try to read mCountLapFlag to determine penalty type
            # 0 = stop & go (don't count lap or time)
            # 1 = drive through (count lap but not time)
            # 2 = normal (count lap and time - no penalty)
            try:
                count_lap_flag = api.shmm.lmuScorVeh(driver_index).mCountLapFlag
                if count_lap_flag == 0:
                    return f"SG({penalties})"
                elif count_lap_flag == 1:
                    return f"DT({penalties})"
            except (AttributeError, IndexError):
                pass
            # Fallback - just show penalty count
            return f"PEN({penalties})"
        except (AttributeError, IndexError):
            return ""

    @staticmethod
    def _get_penalty_reason(driver_index: int) -> str:
        """Get penalty reason for selected driver"""
        try:
            penalties = api.read.vehicle.number_penalties(driver_index)
            if penalties > 0:
                try:
                    count_lap_flag = api.shmm.lmuScorVeh(driver_index).mCountLapFlag
                    if count_lap_flag == 0:
                        penalty_type = "Stop & Go"
                    elif count_lap_flag == 1:
                        penalty_type = "Drive Through"
                    else:
                        penalty_type = "Penalty"
                except (AttributeError, IndexError):
                    penalty_type = "Penalty"
                return f"{penalty_type} ({penalties} pending)"
            return ""
        except (AttributeError, IndexError):
            return ""

    def _reset_timing(self):
        """Reset timing and damage labels to default"""
        self.label_current_lap.setText("--")
        self.label_best_lap.setText("--")
        self.label_last_lap.setText("--")
        self.label_sector1.setText("--")
        self.label_sector2.setText("--")
        self.label_sector3.setText("--")
        self.label_best_s1.setText("--")
        self.label_best_s2.setText("--")
        self.label_best_s3.setText("--")
        self.label_gap_leader.setText("--")
        self.label_gap_next.setText("--")
        self.label_laps.setText("--")
        self.label_speed.setText("--")
        self.label_top_speed.setText("--")
        self._top_speeds.clear()
        self.label_penalty.setText("--")
        self.label_integrity.setText("Integrity: --")
        self.bar_integrity.setValue(100)
        self.bar_integrity.setStyleSheet(
            "QProgressBar { background: #444; border: none; border-radius: 3px; }"
            "QProgressBar::chunk { background: #2ecc71; border-radius: 3px; }"
        )
        self.label_body.setText("Body: OK")
        self.label_aero.setText("Aero: OK")
        self.label_susp.setText("Susp: OK")
        self.label_detached.setText("")
        self.label_energy.setText("Energy: --")
        self.bar_energy.setValue(0)
        self.bar_energy.setStyleSheet(
            "QProgressBar { background: #333; border: 1px solid #555; border-radius: 4px; }"
            "QProgressBar::chunk { background: qlineargradient("
            "x1:0, y1:0, x2:1, y2:0, stop:0 #2980b9, stop:1 #3498db); border-radius: 3px; }"
        )

    def _update_timing(self):
        """Update live timing and damage for spectated driver"""
        if not cfg.api["enable_player_index_override"]:
            return

        # Auto-refresh driver list (~every 1s)
        self._list_update_counter += 1
        if self._list_update_counter >= 10:
            self._list_update_counter = 0
            self._refresh_list_only()

        index = cfg.api["player_index"]
        if index < 0:
            return

        try:
            total = api.read.vehicle.total_vehicles()
            if index >= total:
                return

            # Current lap time
            current = api.read.timing.current_laptime(index)
            self.label_current_lap.setText(f"<b>{self._format_time(current)}</b>")

            # Lap count
            laps = api.read.lap.completed_laps(index)
            self.label_laps.setText(f"<b>{laps}</b>")

            # Best & last lap
            best = api.read.timing.best_laptime(index)
            last = api.read.timing.last_laptime(index)
            self.label_best_lap.setText(f"<b>{self._format_time(best)}</b>")
            self.label_last_lap.setText(f"<b>{self._format_time(last)}</b>")

            # Current sectors
            cur_s1 = api.read.timing.current_sector1(index)
            cur_s2 = api.read.timing.current_sector2(index)
            last_s1 = api.read.timing.last_sector1(index)
            last_s2 = api.read.timing.last_sector2(index)
            last_lap = api.read.timing.last_laptime(index)

            # Derive individual sector times
            s1_time = cur_s1 if cur_s1 > 0 else last_s1
            s2_time = (cur_s2 - cur_s1) if cur_s1 > 0 and cur_s2 > 0 else (
                (last_s2 - last_s1) if last_s1 > 0 and last_s2 > 0 else 0)
            s3_time = (last_lap - last_s2) if last_s2 > 0 and last_lap > 0 else 0

            self.label_sector1.setText(f"<b>{self._format_time(s1_time)}</b>")
            self.label_sector2.setText(f"<b>{self._format_time(s2_time)}</b>")
            self.label_sector3.setText(f"<b>{self._format_time(s3_time)}</b>")

            # Best sectors
            best_s1 = api.read.timing.best_sector1(index)
            best_s2 = api.read.timing.best_sector2(index)
            best_s2_individual = (best_s2 - best_s1) if best_s1 > 0 and best_s2 > 0 else 0
            best_s3 = (best - best_s2) if best_s2 > 0 and best > 0 else 0

            self.label_best_s1.setText(f"<b>{self._format_time(best_s1)}</b>")
            self.label_best_s2.setText(f"<b>{self._format_time(best_s2_individual)}</b>")
            self.label_best_s3.setText(f"<b>{self._format_time(best_s3)}</b>")

            # Gaps
            gap_leader = api.read.timing.behind_leader(index)
            gap_next = api.read.timing.behind_next(index)
            gap_leader_str = f"<b>+{gap_leader:.3f}</b>" if gap_leader > 0 else "--"
            gap_next_str = f"<b>+{gap_next:.3f}</b>" if gap_next > 0 else "--"
            self.label_gap_leader.setText(gap_leader_str)
            self.label_gap_next.setText(gap_next_str)

            # Speed & top speed
            speed_ms = api.read.vehicle.speed(index)
            speed_kph = speed_ms * 3.6
            if index not in self._top_speeds or speed_ms > self._top_speeds[index]:
                self._top_speeds[index] = speed_ms
            top_speed_kph = self._top_speeds[index] * 3.6
            self.label_speed.setText(f"<b>{speed_kph:.0f} km/h</b>")
            self.label_top_speed.setText(f"<b>{top_speed_kph:.0f} km/h</b>")

            # Penalty
            penalty_reason = self._get_penalty_reason(index)
            if penalty_reason:
                self.label_penalty.setText(f"<b style='color:#e74c3c'>{penalty_reason}</b>")
            else:
                self.label_penalty.setText("--")

            # Damage & energy
            self._update_damage(index)
            self._update_energy(index)

        except (AttributeError, IndexError):
            pass

    def _update_energy(self, index: int):
        """Update virtual energy bar for spectated driver"""
        try:
            veh_data = minfo.vehicles.dataSet[index]
            ve_remaining = veh_data.energyRemaining
            if ve_remaining > -1.0 and veh_data.driverName:
                pct_e = max(0.0, min(1.0, ve_remaining))
                pct_e_int = int(pct_e * 1000)
                self.bar_energy.setValue(pct_e_int)
                self.label_energy.setText(f"Energy: <b>{pct_e:.1%}</b>")
                if pct_e > 0.4:
                    color = "qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #2980b9, stop:1 #3498db)"
                elif pct_e > 0.15:
                    color = "qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #d35400, stop:1 #f39c12)"
                else:
                    color = "qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #c0392b, stop:1 #e74c3c)"
                self.bar_energy.setStyleSheet(
                    "QProgressBar { background: #333; border: 1px solid #555; border-radius: 4px; }"
                    f"QProgressBar::chunk {{ background: {color}; border-radius: 3px; }}"
                )
            else:
                self.bar_energy.setValue(0)
                self.label_energy.setText("Energy: <b>N/A</b>")
        except (AttributeError, IndexError):
            pass

    def _update_damage(self, index: int):
        """Update damage indicators for spectated driver"""
        try:
            # Integrity
            integrity = api.read.vehicle.integrity(index)
            pct = max(0, min(100, int(integrity * 100)))
            self.label_integrity.setText(f"Integrity: <b>{pct}%</b>")
            self.bar_integrity.setValue(pct)
            if pct > 70:
                color = "#2ecc71"  # green
            elif pct > 40:
                color = "#f39c12"  # orange
            else:
                color = "#e74c3c"  # red
            self.bar_integrity.setStyleSheet(
                "QProgressBar { background: #444; border: none; border-radius: 3px; }"
                f"QProgressBar::chunk {{ background: {color}; border-radius: 3px; }}"
            )

            # Body damage
            dmg = api.read.vehicle.damage_severity(index)
            body_total = sum(dmg)
            if body_total == 0:
                self.label_body.setText("Body: <b>OK</b>")
            elif body_total <= 4:
                self.label_body.setText(f"Body: <b style='color:#f39c12'>Minor ({body_total})</b>")
            else:
                self.label_body.setText(f"Body: <b style='color:#e74c3c'>Heavy ({body_total})</b>")

            # Aero damage
            aero = api.read.vehicle.aero_damage(index)
            if aero <= 0:
                self.label_aero.setText("Aero: <b>OK</b>")
            elif aero < 0.5:
                self.label_aero.setText(f"Aero: <b style='color:#f39c12'>{aero:.0%}</b>")
            else:
                self.label_aero.setText(f"Aero: <b style='color:#e74c3c'>{aero:.0%}</b>")

            # Suspension damage
            susp = api.read.wheel.suspension_damage(index)
            susp_total = sum(susp) if susp else 0
            if susp_total <= 0:
                self.label_susp.setText("Susp: <b>OK</b>")
            else:
                self.label_susp.setText(f"Susp: <b style='color:#e74c3c'>DMG</b>")

            # Detached parts
            if api.read.vehicle.is_detached(index):
                self.label_detached.setText("<b style='color:#e74c3c'>PARTS DETACHED</b>")
            else:
                self.label_detached.setText("")

        except (AttributeError, IndexError):
            pass
