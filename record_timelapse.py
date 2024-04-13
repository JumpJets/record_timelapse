"""
Record timelapse

```
pip install pywin32 textual
```

ffmpeg:
```
ffmpeg -f gdigrab -thread_queue_size 1024 -rtbufsize 256M -framerate 1 -offset_x 0 -offset_y 0 -video_size 1920x1080 -show_region 0 -i desktop -filter_complex settb=1/60,setpts=N/TB/60 -c:v h264_nvenc -r 60 -preset p4 -tune hq -b:v 4000k -movflags +faststart -y "rec_win.mkv"
```
"""  # noqa: E501

from typing import Any, ClassVar, NoReturn, Self
import asyncio
from collections.abc import Generator, Sequence
import contextlib
import ctypes
from datetime import datetime
from fractions import Fraction
import os
from pathlib import Path
import signal
from subprocess import CREATE_NEW_PROCESS_GROUP, PIPE, Popen
import sys
from time import monotonic, sleep

import pip

try:
    from textual import on
    from textual.app import App
    from textual.binding import Binding
    from textual.reactive import reactive
    from textual.validation import Number
    from textual.widget import Widget
    from textual.widgets import Button, Footer, Header, Input, Label, Select, Static
    from win32gui import EnumWindows, GetWindowRect, GetWindowText, IsWindowVisible
except ImportError:
    print("Installing required libs: pywin32 and textual")
    pip.main(["install", "pywin32", "textual"])

    from textual import on
    from textual.app import App
    from textual.binding import Binding
    from textual.reactive import reactive
    from textual.validation import Number
    from textual.widget import Widget
    from textual.widgets import Button, Footer, Header, Input, Label, Select, Static
    from win32gui import EnumWindows, GetWindowRect, GetWindowText, IsWindowVisible

user32 = ctypes.windll.user32
WINDOW_MAX_WIDTH, WINDOW_MAX_HEIGHT = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
PID = 0


class TimeDisplayWidget(Static):
    """
    A widget to display elapsed time.
    """

    start_time = reactive(monotonic)
    time = reactive(0.0)

    def on_mount(self: Self) -> None:
        """
        Event handler called when widget is added to the app.
        """

        self.update_timer = self.set_interval(1 / 60, self.update_time, pause=True)

    def update_time(self: Self) -> None:
        """
        Update the time to the current time.
        """

        self.time = monotonic() - self.start_time

    def watch_time(self: Self, time: float) -> None:
        """
        Called when the time attribute changes.
        """

        minutes, seconds = divmod(time, 60)
        hours, minutes = divmod(minutes, 60)
        self.update(f"{hours:02,.0f}:{minutes:02.0f}:{seconds:05.2f}")

    def start(self: Self) -> None:
        """
        Method to start time updating.
        """

        self.start_time = monotonic()
        self.time = 0
        self.update_timer.reset()

    def stop(self: Self) -> None:
        """
        Method to stop the time display updating.
        """

        self.update_timer.pause()
        self.update_time()

    def cancel(self: Self) -> None:
        """
        Method to reset the time display to zero.
        """

        self.update_timer.stop()
        self.time = 0


class BoxSize(Widget):
    """
    Reactive box size
    """

    box: reactive[tuple[int, int, int, int]] = reactive((0, 0, 1, 1), recompose=True)

    def compose(self: Self) -> Generator[Label, Any, None]:
        """
        Label
        """

        yield Label(f"Selected window size: {self.box}")


class RecorderApp(App):
    """
    Record timelapse app
    """

    TITLE = "Record timelapse"

    CSS_PATH = "record_timelapse.tcss"
    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        ("x", "exit", "Exit"),
        ("r", "start_recording", "Start recording"),
        ("s", "stop_recording", "Stop recording"),
        ("c", "cancel_recording", "Cancel recording"),
    ]
    WINDOWS: ClassVar[dict[int, str]] = {}

    box = reactive((0, 0, 1, 1))
    hwid: int = 0
    process = None
    started = False
    record_fps = 1
    target_fps = 60
    bitrate = 4000

    def compose(self: Self) -> Generator[Widget, Any, None]:
        """
        Widgets for app
        """

        yield Header()
        yield Footer()

        yield Select(self.get_windows_titles(), prompt="Select window")

        yield BoxSize(id="win_box")
        yield Input(placeholder="Record FPS (default 1)", type="number", validators=[Number(minimum=0.001, maximum=1000)], id="record_fps")
        yield Input(placeholder="Target FPS (default 60)", type="number", validators=[Number(minimum=12, maximum=1000)], id="target_fps")
        yield Input(placeholder="Bitrate (KB, default 4000)", type="integer", validators=[Number(minimum=100, maximum=400000)], id="bitrate")
        yield TimeDisplayWidget()

        # yield Log(max_lines=100)

        yield Button("Start", id="start", variant="success", disabled=True)
        yield Button("Stop", id="stop", variant="error")
        yield Button("Cancel", id="cancel")

    async def action_exit(self: Self) -> NoReturn:
        """
        Exit program
        """

        await self.action_stop_recording()

        sys.exit()

    def get_windows_callback(self: Self, hwnd: int, extra: Any) -> None:
        """
        EnumWindows callback
        """

        if IsWindowVisible(hwnd) and (text := GetWindowText(hwnd)):
            self.WINDOWS[hwnd] = text
            # print(f"Window {hwnd: >9}: {text!r}, {extra!r}")

    def action_start_recording(self: Self) -> None:
        """
        Start recording
        """

        if self.started or not self.hwid or all(v < 0 for v in self.box):
            return

        self.add_class("started")
        self.start_recording()
        self.query_one(TimeDisplayWidget).start()
        self.query_one(Select).disabled = True
        self.started = True

    async def action_stop_recording(self: Self) -> None:
        """
        Stop recording
        """

        if not self.started:
            return

        self.query_one(TimeDisplayWidget).stop()
        await self.stop_recording()
        self.remove_class("started")
        self.query_one(Select).disabled = False
        self.started = False

    async def action_cancel_recording(self: Self) -> None:
        """
        Cancel recording
        """

        if not self.started:
            return

        self.query_one(TimeDisplayWidget).stop()  # .cancel()
        await self.cancel_recording()
        self.remove_class("started")
        self.query_one(Select).disabled = False
        self.started = False

    def get_windows_titles(self: Self) -> Sequence[tuple[str, int]]:
        """
        Get windows titles
        """

        self.WINDOWS.clear()
        EnumWindows(self.get_windows_callback, None)
        return [(f"{text} ({hwid})", hwid) for hwid, text in sorted(self.WINDOWS.items(), key=lambda x: x[1].lower())]

    def update_win_dimensions(self: Self) -> None:
        """
        Update selected window dimensions
        """

        if not self.hwid:
            return

        self.box = GetWindowRect(self.hwid)
        # self.attrs = GetLayeredWindowAttributes(self.hwid)

        # Windows 10 border 8px
        if self.box[0] == -8 or self.box[1] == -8 or self.box[2] == WINDOW_MAX_WIDTH + 8 or self.box[3] == WINDOW_MAX_HEIGHT + 8:
            self.box = (self.box[0] + 8, self.box[1] + 8, self.box[2] - 8, self.box[3] - 8)

        self.query_one(BoxSize).box = self.box

    async def on_button_pressed(self: Self, event: Button.Pressed) -> None:
        """
        Event handler called when a button is pressed.
        """

        button_id = event.button.id
        time_display = self.query_one(TimeDisplayWidget)
        select = self.query_one(Select)

        if button_id == "start" and not all(v < 0 for v in self.box):
            self.add_class("started")
            self.start_recording()
            time_display.start()
            select.disabled = True
            self.started = True

        elif button_id == "stop":
            time_display.stop()
            await self.stop_recording()
            self.remove_class("started")
            select.disabled = False
            self.started = False

        elif event.button.id == "cancel":
            # time_display.cancel()
            time_display.stop()
            await self.cancel_recording()
            self.remove_class("started")
            select.disabled = False
            self.started = False

    @on(Select.Changed)
    def select_changed(self: Self, event: Select.Changed) -> None:
        """
        Track select event
        """

        if not event.value or event.value == Select.BLANK:
            self.hwid = 0
            self.box = (0, 0, 1, 1)
            self.query_one(BoxSize).box = self.box
            self.query_one("#start").disabled = True
            return

        self.hwid = event.value  # type: ignore[int]
        self.update_win_dimensions()

        self.query_one("#start").disabled = False

    @on(Input.Changed)
    def input_update(self: Self, event: Input.Changed) -> None:
        """
        On input update
        """

        if not event.validation_result or not event.validation_result.is_valid or not event.value:
            return

        if event.input.id == "record_fps":
            self.record_fps = float(event.value)
        elif event.input.id == "target_fps":
            self.target_fps = float(event.value)
        elif event.input.id == "bitrate":
            self.bitrate = int(event.value)

    def start_recording(self: Self) -> None:
        """
        Start recording
        """

        global PID

        if self.process:
            return

        if isinstance(self.record_fps, int) and isinstance(self.target_fps, int):
            settb = f"1/{self.target_fps}"
            setpts = self.target_fps
        else:
            _settb = Fraction(self.record_fps / self.target_fps).limit_denominator()
            settb = f"{_settb.numerator}/{_settb.denominator}"
            _setpts = Fraction(self.target_fps).limit_denominator()
            setpts = f"({_setpts.numerator}/{_setpts.denominator})"

        # fmt: off
        self.cmd = [
            "ffmpeg",
            "-v", "quiet",
            "-stats",
            "-f", "gdigrab",
            "-thread_queue_size", "1024",
            "-rtbufsize", "256M",
            "-framerate", f"{self.record_fps}",
            "-offset_x", f"{max(self.box[0], 0)}",
            "-offset_y", f"{max(self.box[1], 0)}",
            "-video_size", f"{min(self.box[2], WINDOW_MAX_WIDTH)}x{min(self.box[3], WINDOW_MAX_HEIGHT)}",
            "-show_region", "0",
            "-i", "desktop",
            "-filter_complex", f"settb={settb},setpts=N/TB/{setpts}",
            "-c:v", "h264_nvenc",
            "-r", f"{self.target_fps}",
            "-preset", "p4",
            "-tune", "hq",
            "-b:v", f"{self.bitrate}k",
            "-movflags", "+faststart",
            "-y",
            f"recordings/timelapse_{datetime.now().strftime('%Y.%m.%d_%H.%M.%S')}.mkv",  # noqa: DTZ005
        ]
        # fmt: on

        self.process = Popen(self.cmd, stdin=PIPE, creationflags=CREATE_NEW_PROCESS_GROUP)
        PID = self.process.pid

    async def stop_recording(self: Self) -> None:
        """
        Stop recording
        """

        global PID

        if not self.process:
            return

        self.process.send_signal(signal.CTRL_BREAK_EVENT)
        self.process = None
        PID = 0

        await asyncio.sleep(3)
        await self.recompose()

    async def cancel_recording(self: Self) -> None:
        """
        Cancel recording
        """

        global PID

        if not self.process:
            return

        self.process.send_signal(signal.SIGTERM)
        self.process = None
        PID = 0

        await asyncio.sleep(3)
        await self.recompose()

        file = Path(self.cmd[-1])
        file.unlink(True)


if __name__ == "__main__":
    if not (recordings := Path("recordings")).is_dir():
        recordings.mkdir(exist_ok=True)

    try:
        app = RecorderApp()
        app.run()
    except Exception:
        if PID:
            try:
                os.kill(PID, signal.CTRL_BREAK_EVENT)
            except Exception as e:
                print(f"Stopping ffmpeg with {PID=} result in an error {e}")
            sleep(5)
            with contextlib.suppress(Exception):
                os.kill(PID, signal.SIGTERM)
            sys.exit(PID)
        raise
