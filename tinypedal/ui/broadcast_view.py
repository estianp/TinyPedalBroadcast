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
from PySide2.QtGui import QColor
from PySide2.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .. import app_signal
from ..api_control import api
from ..calculation import sec2laptime
from ..const_common import MAX_SECONDS
from ..setting import cfg
from ._common import UIScaler

logger = logging.getLogger(__name__)


SORT_STANDINGS = 0
SORT_RELATIVE = 1
SORT_LABELS = ("Standings", "Relative")
BATTLE_THRESHOLD = 1.0  # seconds
CLOSE_THRESHOLD = 2.0  # seconds
YELLOW_SPEED_THRESHOLD = 8  # m/s
YELLOW_STICKY_DURATION = 5.0  # seconds to keep yellow highlight after clearing
COLOR_BATTLE = QColor(220, 30, 30)  # red
COLOR_CLOSE = QColor(255, 140, 0)  # orange
COLOR_YELLOW = QColor(255, 255, 0)  # yellow
COLOR_BLUE = QColor(0, 160, 255)  # blue
COLOR_PIT = QColor(150, 150, 150)  # grey


class BroadcastList(QWidget):
    """Broadcast list view"""

    def __init__(self, parent):
        super().__init__(parent)
        self.last_enabled = None
        self._sort_mode = SORT_STANDINGS
        self._yellow_timestamps = {}  # driver_index -> last time yellow was active

        # Label
        self.label_spectating = QLabel("")

        # List box
        self.listbox_spectate = QListWidget(self)
        self.listbox_spectate.setAlternatingRowColors(True)
        self.listbox_spectate.itemDoubleClicked.connect(self.spectate_selected)

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

        # Timing info panel
        self.timing_group = QGroupBox("Live Timing")
        timing_layout = QVBoxLayout()

        self.label_current_lap = QLabel("Current Lap: --")
        self.label_best_lap = QLabel("Best Lap: --")
        self.label_last_lap = QLabel("Last Lap: --")
        self.label_sector1 = QLabel("S1: --")
        self.label_sector2 = QLabel("S2: --")
        self.label_sector3 = QLabel("S3: --")
        self.label_best_s1 = QLabel("Best S1: --")
        self.label_best_s2 = QLabel("Best S2: --")
        self.label_best_s3 = QLabel("Best S3: --")
        self.label_gap_leader = QLabel("Gap to Leader: --")
        self.label_gap_next = QLabel("Gap to Next: --")
        self.label_laps = QLabel("Laps: --")

        row_current = QHBoxLayout()
        row_current.addWidget(self.label_current_lap)
        row_current.addWidget(self.label_laps)

        row_times = QHBoxLayout()
        row_times.addWidget(self.label_best_lap)
        row_times.addWidget(self.label_last_lap)

        row_sectors = QHBoxLayout()
        row_sectors.addWidget(self.label_sector1)
        row_sectors.addWidget(self.label_sector2)
        row_sectors.addWidget(self.label_sector3)

        row_best_sectors = QHBoxLayout()
        row_best_sectors.addWidget(self.label_best_s1)
        row_best_sectors.addWidget(self.label_best_s2)
        row_best_sectors.addWidget(self.label_best_s3)

        row_gaps = QHBoxLayout()
        row_gaps.addWidget(self.label_gap_leader)
        row_gaps.addWidget(self.label_gap_next)

        timing_layout.addLayout(row_current)
        timing_layout.addLayout(row_times)
        timing_layout.addLayout(row_sectors)
        timing_layout.addLayout(row_best_sectors)
        timing_layout.addLayout(row_gaps)
        self.timing_group.setLayout(timing_layout)

        # Layout
        layout_main = QVBoxLayout()
        layout_main.addWidget(self.label_spectating)
        layout_main.addWidget(self.listbox_spectate)
        layout_main.addLayout(layout_button)
        layout_main.addWidget(self.timing_group)
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
        if enabled:
            logger.info("ENABLED: broadcast mode")
            self._timing_timer.start()
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

        if self._sort_mode == SORT_RELATIVE:
            driver_list.sort(key=lambda x: abs(x[4]))  # sort by absolute gap
        else:
            driver_list.sort(key=lambda x: x[0])  # sort by position

        listbox.clear()

        anon_item = QListWidgetItem("Anonymous")
        anon_item.setData(Qt.UserRole, "Anonymous")
        listbox.addItem(anon_item)

        for place, class_name, name, _index, rel_gap, in_pits, is_yellow, is_blue in driver_list:
            class_pos = class_positions.get(_index, place)
            tags = ""
            if in_pits:
                tags += "  [PIT]"
            if is_yellow:
                tags += "  [YELLOW]"
            if is_blue:
                tags += "  [BLUE]"
            if not is_yellow and not is_blue and _index in battles:
                tags += "  [BATTLE]"
            if self._sort_mode == SORT_RELATIVE:
                gap_str = f"+{rel_gap:.1f}" if rel_gap >= 0 else f"{rel_gap:.1f}"
                display_text = f"{gap_str}s  P{class_pos}  [{class_name}]  {name}{tags}"
            else:
                display_text = f"P{class_pos}  [{class_name}]  {name}{tags}"
            item = QListWidgetItem(display_text)
            item.setData(Qt.UserRole, name)
            if is_yellow:
                item.setForeground(COLOR_YELLOW)
            elif is_blue:
                item.setForeground(COLOR_BLUE)
            elif in_pits:
                item.setForeground(COLOR_PIT)
            elif _index in battles:
                item.setForeground(COLOR_BATTLE)
            elif _index in close:
                item.setForeground(COLOR_CLOSE)
            listbox.addItem(item)

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
        if selected_index < 0:
            return

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

        if self._sort_mode == SORT_RELATIVE:
            driver_list.sort(key=lambda x: abs(x[4]))  # sort by absolute gap
        else:
            driver_list.sort(key=lambda x: x[0])  # sort by position

        listbox.clear()

        anon_item = QListWidgetItem("Anonymous")
        anon_item.setData(Qt.UserRole, "Anonymous")
        listbox.addItem(anon_item)

        for place, class_name, name, _index, rel_gap, in_pits, is_yellow, is_blue in driver_list:
            class_pos = class_positions.get(_index, place)
            tags = ""
            if in_pits:
                tags += "  [PIT]"
            if is_yellow:
                tags += "  [YELLOW]"
            if is_blue:
                tags += "  [BLUE]"
            if not is_yellow and not is_blue and _index in battles:
                tags += "  [BATTLE]"
            if self._sort_mode == SORT_RELATIVE:
                gap_str = f"+{rel_gap:.1f}" if rel_gap >= 0 else f"{rel_gap:.1f}"
                display_text = f"{gap_str}s  P{class_pos}  [{class_name}]  {name}{tags}"
            else:
                display_text = f"P{class_pos}  [{class_name}]  {name}{tags}"
            item = QListWidgetItem(display_text)
            item.setData(Qt.UserRole, name)
            if is_yellow:
                item.setForeground(COLOR_YELLOW)
            elif is_blue:
                item.setForeground(COLOR_BLUE)
            elif in_pits:
                item.setForeground(COLOR_PIT)
            elif _index in battles:
                item.setForeground(COLOR_BATTLE)
            elif _index in close:
                item.setForeground(COLOR_CLOSE)
            listbox.addItem(item)

        self.focus_on_selected(selected_driver_name)

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
    def _format_time(seconds: float) -> str:
        """Format time value for display"""
        if seconds <= 0 or seconds >= MAX_SECONDS:
            return "-:--.---"
        return sec2laptime(seconds)

    def _reset_timing(self):
        """Reset timing labels to default"""
        self.label_current_lap.setText("Current Lap: --")
        self.label_best_lap.setText("Best Lap: --")
        self.label_last_lap.setText("Last Lap: --")
        self.label_sector1.setText("S1: --")
        self.label_sector2.setText("S2: --")
        self.label_sector3.setText("S3: --")
        self.label_best_s1.setText("Best S1: --")
        self.label_best_s2.setText("Best S2: --")
        self.label_best_s3.setText("Best S3: --")
        self.label_gap_leader.setText("Gap to Leader: --")
        self.label_gap_next.setText("Gap to Next: --")
        self.label_laps.setText("Laps: --")

    def _update_timing(self):
        """Update live timing for spectated driver"""
        index = cfg.api["player_index"]
        if index < 0 or not cfg.api["enable_player_index_override"]:
            return

        # Auto-refresh driver list (~every 1s)
        self._list_update_counter += 1
        if self._list_update_counter >= 10:
            self._list_update_counter = 0
            self._refresh_list_only()

        try:
            total = api.read.vehicle.total_vehicles()
            if index >= total:
                return

            # Current lap time
            current = api.read.timing.current_laptime(index)
            self.label_current_lap.setText(f"Current Lap: <b>{self._format_time(current)}</b>")

            # Lap count
            laps = api.read.lap.completed_laps(index)
            self.label_laps.setText(f"Laps: <b>{laps}</b>")

            # Best & last lap
            best = api.read.timing.best_laptime(index)
            last = api.read.timing.last_laptime(index)
            self.label_best_lap.setText(f"Best Lap: <b>{self._format_time(best)}</b>")
            self.label_last_lap.setText(f"Last Lap: <b>{self._format_time(last)}</b>")

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

            self.label_sector1.setText(f"S1: <b>{self._format_time(s1_time)}</b>")
            self.label_sector2.setText(f"S2: <b>{self._format_time(s2_time)}</b>")
            self.label_sector3.setText(f"S3: <b>{self._format_time(s3_time)}</b>")

            # Best sectors
            best_s1 = api.read.timing.best_sector1(index)
            best_s2 = api.read.timing.best_sector2(index)
            best_s2_individual = (best_s2 - best_s1) if best_s1 > 0 and best_s2 > 0 else 0
            best_s3 = (best - best_s2) if best_s2 > 0 and best > 0 else 0

            self.label_best_s1.setText(f"Best S1: <b>{self._format_time(best_s1)}</b>")
            self.label_best_s2.setText(f"Best S2: <b>{self._format_time(best_s2_individual)}</b>")
            self.label_best_s3.setText(f"Best S3: <b>{self._format_time(best_s3)}</b>")

            # Gaps
            gap_leader = api.read.timing.behind_leader(index)
            gap_next = api.read.timing.behind_next(index)
            gap_leader_str = f"+{gap_leader:.3f}" if gap_leader > 0 else "--"
            gap_next_str = f"+{gap_next:.3f}" if gap_next > 0 else "--"
            self.label_gap_leader.setText(f"Gap to Leader: <b>{gap_leader_str}</b>")
            self.label_gap_next.setText(f"Gap to Next: <b>{gap_next_str}</b>")

        except (AttributeError, IndexError):
            pass
