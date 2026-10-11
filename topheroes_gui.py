"""Desktop controls for the pixel-only Top Heroes helper."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import os
import queue
import sys
import threading
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import ttk

import cv2
import win32con
import win32gui
from PIL import Image, ImageTk

from crystal_gather import CrystalGatherer, GameSession, NoIdleQueues, RunStopped, read_gather_config
from top_heroes import WorkflowError, client_box, find_game_window as find_window, game_windows


RESOURCE_ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
DATA_ROOT = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "TopHeroesHelper"
BG, PANEL, FIELD = "#101825", "#192536", "#233247"
TEXT, MUTED, ACCENT = "#edf3fa", "#9eafc4", "#60dfca"
NAMES = {"world": "进入世界", "search_icon": "打开搜索", "resources": "资源采集",
         "red_crystal": "选择红水晶", "minus": "降低等级", "plus": "提高等级",
         "search_button": "搜索据点", "gather": "采集", "depart": "出发", "idle": "空闲队列"}


class HelperApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.events: queue.Queue = queue.Queue()
        self.stop_event = threading.Event()
        self.closing = threading.Event()
        self.hotkey_thread_id = 0
        self.hotkey_ready = threading.Event()
        self.worker: threading.Thread | None = None
        self.busy = False
        self.active_mode = None
        self.countdown_id = None
        self.lines: list[str] = []
        self.photo = None
        self.last_frame_time = 0.0
        self.settings = self.load_settings()
        DATA_ROOT.mkdir(parents=True, exist_ok=True)
        self.log_path = DATA_ROOT / "logs" / (datetime.now().strftime("%Y%m%d-%H%M%S") + ".log")
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.title_var = tk.StringVar(value=self.settings.get("title", "Steam Top Heroes"))
        self.count_var = tk.StringVar(value=str(self.settings.get("dispatches", 3)))
        self.delay_var = tk.StringVar(value=str(self.settings.get("delay", 5)))
        self.continuous_var = tk.BooleanVar(value=self.settings.get("continuous", True))
        self.monitor_var = tk.StringVar(value="队伍倒计时：等待识别")
        self.alarm_var = tk.StringVar(value="下一次检查：—")
        self.next_alarm = None
        self.monitor_rows = []
        self.monitor_seen = 0.0
        self.status_var = tk.StringVar(value="等待开始")
        self.detail_var = tk.StringVar(value="启动时恢复游戏画面为1600×900，再识别并采集。")
        self.size_var = tk.StringVar(value="未连接")
        self.idle_var = tk.StringVar(value="—")
        self.progress_var = tk.StringVar(value="0 / 3")
        self.build_ui()
        self.refresh_windows()
        self.add_log("已就绪：游戏状态只从截图识别，动作使用鼠标点击。")
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.after(100, self.drain_events)
        threading.Thread(target=self.hotkey_loop, daemon=True).start()

    def load_settings(self) -> dict:
        try:
            value = json.loads((DATA_ROOT / "settings.json").read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}

    def label(self, parent, text=None, variable=None, size=11, color=TEXT, bg=PANEL, bold=False):
        return tk.Label(parent, text=text, textvariable=variable, bg=bg, fg=color,
                        font=("Microsoft YaHei UI", size, "bold" if bold else "normal"), anchor="w")

    def button(self, parent, text, command, primary=False):
        return tk.Button(parent, text=text, command=command, bg=ACCENT if primary else FIELD,
                         fg=BG if primary else TEXT, activebackground="#85eadb" if primary else "#34465e",
                         activeforeground=BG if primary else TEXT, disabledforeground=MUTED,
                         relief="flat", borderwidth=0, cursor="hand2", padx=15, pady=8,
                         font=("Microsoft YaHei UI", 11, "bold"))

    def build_ui(self):
        root = self.root
        root.title("Top Heroes · 日常助手")
        root.geometry("1100x820")
        root.minsize(980, 790)
        root.configure(bg=BG)
        root.iconphoto(True, ImageTk.PhotoImage(Image.open(RESOURCE_ROOT / "templates/world.png")))
        style = ttk.Style(root)
        style.theme_use("clam")
        style.configure("TCombobox", fieldbackground=FIELD, background=FIELD, foreground=TEXT,
                        arrowcolor=TEXT, padding=7)
        style.map("TCombobox", fieldbackground=[("readonly", FIELD)], foreground=[("readonly", TEXT)])
        style.configure("TSpinbox", fieldbackground=FIELD, background=FIELD, foreground=TEXT,
                        arrowcolor=TEXT, padding=7)
        style.configure("Horizontal.TProgressbar", background=ACCENT, troughcolor=FIELD, borderwidth=0)
        root.grid_columnconfigure(1, weight=1)
        root.grid_rowconfigure(0, weight=1)

        side = tk.Frame(root, bg=PANEL, width=280, padx=22, pady=18)
        side.grid(row=0, column=0, sticky="nsew", padx=(18, 0), pady=18)
        side.grid_propagate(False)
        self.label(side, "TOP HEROES", size=12, color=ACCENT, bold=True).pack(anchor="w")
        self.label(side, "日常助手", size=25, bold=True).pack(anchor="w", pady=(3, 8))
        self.label(side, "截图识别 · 鼠标操作", color=MUTED).pack(anchor="w", pady=(0, 12))
        self.label(side, "任务", color=MUTED).pack(anchor="w")
        self.label(side, "红水晶采集", size=17, bold=True).pack(anchor="w", pady=(6, 4))
        self.label(side, "目标等级  6级", color=ACCENT).pack(anchor="w", pady=(0, 12))
        self.label(side, "游戏窗口", color=MUTED).pack(anchor="w")
        self.window_combo = ttk.Combobox(side, textvariable=self.title_var, width=22, state="readonly")
        self.window_combo.pack(fill="x", pady=(6, 6))
        self.window_combo.bind("<<ComboboxSelected>>", lambda _event: self.refresh_windows())
        self.refresh_button = self.button(side, "刷新窗口", self.refresh_windows)
        self.refresh_button.pack(fill="x", pady=(0, 12))
        self.label(side, "目标在外队伍数 / 单轮派队数", color=MUTED).pack(anchor="w")
        self.count_spin = ttk.Spinbox(side, from_=1, to=3, textvariable=self.count_var, width=6, state="readonly")
        self.count_spin.pack(fill="x", pady=(6, 10))
        self.continuous_check = tk.Checkbutton(side, text="持续采集 · 空闲后自动补队",
            variable=self.continuous_var, bg=PANEL, fg=TEXT, selectcolor=FIELD,
            activebackground=PANEL, activeforeground=TEXT, font=("Microsoft YaHei UI", 10))
        self.continuous_check.pack(anchor="w", pady=(0, 10))
        self.label(side, "启动倒计时（秒）", color=MUTED).pack(anchor="w")
        self.delay_spin = ttk.Spinbox(side, from_=2, to=15, textvariable=self.delay_var, width=6, state="readonly")
        self.delay_spin.pack(fill="x", pady=(6, 12))
        self.start_button = self.button(side, "启动采集", lambda: self.start("run"), True)
        self.start_button.pack(fill="x", pady=(0, 8))
        self.preview_button = self.button(side, "预览识别 · 不点击", lambda: self.start("preview"))
        self.preview_button.pack(fill="x", pady=(0, 8))
        self.stop_button = self.button(side, "停止  F8", self.stop)
        self.stop_button.pack(fill="x")
        self.stop_button.configure(state="disabled")
        self.hotkey_label = self.label(side, "F8 可随时停止\n切换离开游戏会停止", color=MUTED, size=9)
        self.hotkey_label.pack(anchor="w", pady=6)

        main = tk.Frame(root, bg=BG, padx=22, pady=24)
        main.grid(row=0, column=1, sticky="nsew", padx=(0, 4), pady=18)
        main.grid_columnconfigure(0, weight=1)
        main.grid_rowconfigure(3, weight=1)
        self.label(main, variable=self.status_var, size=23, bg=BG, bold=True).grid(row=0, column=0, sticky="w")
        self.label(main, variable=self.detail_var, color=MUTED, bg=BG, size=10).grid(row=1, column=0, sticky="w", pady=(7, 20))
        cards = tk.Frame(main, bg=BG)
        cards.grid(row=2, column=0, sticky="ew", pady=(0, 15))
        for index, (name, variable) in enumerate([("窗口尺寸", self.size_var), ("空闲队列", self.idle_var), ("派队记录", self.progress_var)]):
            cards.grid_columnconfigure(index, weight=1, uniform="cards")
            card = tk.Frame(cards, bg=PANEL, padx=14, pady=12)
            card.grid(row=0, column=index, sticky="ew", padx=(0, 8 if index < 2 else 0))
            self.label(card, name, color=MUTED, size=10).pack(anchor="w")
            self.label(card, variable=variable, size=17, bold=True).pack(anchor="w", pady=(3, 0))
        self.canvas = tk.Canvas(main, bg="#0b111c", highlightthickness=0, height=350)
        self.canvas.grid(row=3, column=0, sticky="nsew")
        self.canvas.bind("<Configure>", lambda _event: self.redraw_image())
        self.canvas.create_text(300, 170, text="预览游戏画面与识别位置", fill=MUTED, font=("Microsoft YaHei UI", 13), tags="placeholder")
        self.progress = ttk.Progressbar(main, maximum=3, mode="determinate")
        self.progress.grid(row=4, column=0, sticky="ew", pady=(12, 15))
        timers = tk.Frame(main, bg=BG)
        timers.grid(row=5, column=0, sticky="ew", pady=(0, 7))
        self.label(timers, variable=self.monitor_var, size=10, bg=BG).pack(anchor="w")
        self.label(timers, variable=self.alarm_var, size=10, color=ACCENT, bg=BG).pack(anchor="w", pady=(3, 0))
        log_frame = tk.Frame(main, bg=PANEL)
        log_frame.grid(row=6, column=0, sticky="ew")
        self.log_text = tk.Text(log_frame, height=8, bg=PANEL, fg=TEXT, insertbackground=TEXT,
                                font=("Microsoft YaHei UI", 10), borderwidth=0, padx=12, pady=8, wrap="word", state="disabled")
        scrollbar = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scrollbar.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        footer = tk.Frame(main, bg=BG)
        footer.grid(row=7, column=0, sticky="ew", pady=(10, 0))
        self.label(footer, "游戏画面1600×900 · 尺寸变化自动恢复 · F8停止", color=MUTED, bg=BG, size=9).pack(side="left")
        tk.Button(footer, text="打开日志", command=lambda: os.startfile(self.log_path.parent),
                  fg=ACCENT, bg=BG, activebackground=BG, relief="flat", cursor="hand2").pack(side="right")

    def add_log(self, message: str):
        line = f"[{time.strftime('%H:%M:%S')}] {message}"
        self.lines.append(line)
        self.log_text.configure(state="normal")
        self.log_text.insert("end", line + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")
        try:
            with self.log_path.open("a", encoding="utf-8") as stream:
                stream.write(line + "\n")
        except OSError:
            pass

    def refresh_windows(self):
        titles = [title for _, title in game_windows()]
        self.window_combo.configure(values=titles)
        if len(titles) == 1:
            self.title_var.set(titles[0])
        elif self.title_var.get() not in titles:
            self.title_var.set("")
        try:
            hwnd = find_window(self.title_var.get().strip())
            if win32gui.IsIconic(hwnd):
                self.size_var.set("已找到 · 最小化")
            else:
                box = client_box(hwnd)
                self.size_var.set(f"{box[2] - box[0]} × {box[3] - box[1]}")
        except (WorkflowError, OSError):
            self.size_var.set("未找到游戏")

    def set_busy(self, busy: bool):
        self.busy = busy
        for button in (self.start_button, self.preview_button, self.refresh_button):
            button.configure(state="disabled" if busy else "normal")
        self.stop_button.configure(state="normal" if busy else "disabled")
        for widget in (self.window_combo, self.count_spin, self.delay_spin):
            widget.configure(state="disabled" if busy else "readonly")
        self.continuous_check.configure(state="disabled" if busy else "normal")

    def start(self, mode: str):
        if self.busy:
            return
        try:
            config = read_gather_config(RESOURCE_ROOT / "gather_crystal.yaml")
            title, count, delay = self.title_var.get().strip(), int(self.count_var.get()), int(self.delay_var.get())
            if not title or not 1 <= count <= 3 or not 2 <= delay <= 15:
                raise ValueError("请选择1到3队，以及2到15秒的倒计时")
            find_window(title)
            continuous = self.continuous_var.get()
            config.update(window_title=title, dispatches=count, continuous=continuous)
            (DATA_ROOT / "settings.json").write_text(json.dumps({"title": title, "dispatches": count,
                "delay": delay, "continuous": continuous}, ensure_ascii=False), encoding="utf-8")
        except Exception as exc:
            self.status_var.set("无法开始")
            self.detail_var.set(str(exc))
            self.add_log(str(exc))
            return
        self.stop_event.clear()
        self.active_mode = mode
        self.set_busy(True)
        self.progress_var.set("累计 0 次" if config["continuous"] else f"0 / {count}")
        self.monitor_var.set("队伍倒计时：等待识别")
        self.alarm_var.set("下一次检查：—")
        self.next_alarm, self.monitor_rows = None, []
        self.progress.configure(maximum=count, value=0)
        self.idle_var.set("—")
        self.add_log("开始采集" if mode == "run" else "开始识别预览，不会点击")
        self.count_down(delay, mode, config)

    def count_down(self, remaining: int, mode: str, config: dict):
        if self.stop_event.is_set():
            self.finish("stopped", "已取消启动")
            return
        if remaining:
            self.status_var.set(f"{remaining} 秒后开始")
            self.detail_var.set("即将最小化助手并切换到游戏。可点击停止取消。")
            self.countdown_id = self.root.after(1000, lambda: self.count_down(remaining - 1, mode, config))
            return
        self.countdown_id = None
        try:
            hwnd = find_window(config["window_title"])
            self.add_log(f"连接游戏：{win32gui.GetWindowText(hwnd)}（窗口 {hwnd}）")
            self.root.iconify()
            if win32gui.IsIconic(hwnd):
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            win32gui.SetForegroundWindow(hwnd)
        except Exception as exc:
            self.finish("error", f"无法切换到游戏：{exc}")
            return
        self.status_var.set("采集中" if mode == "run" else "识别中")
        self.detail_var.set("F8 可停止；切换离开游戏也会停止点击。")
        self.worker = threading.Thread(target=self.work, args=(mode, config), daemon=True)
        self.worker.start()

    def frame_event(self, frame):
        now = time.monotonic()
        if now - self.last_frame_time >= 0.7:
            self.last_frame_time = now
            self.events.put(("frame", frame))

    def work(self, mode: str, config: dict):
        try:
            if self.stop_event.wait(0.3):
                raise RunStopped("已停止")
            session = GameSession(config["window_title"], self.frame_event,
                                  target_size=tuple(config["window_size"]), stop_event=self.stop_event,
                                  logger=lambda message: self.events.put(("log", message)))
            bot = CrystalGatherer(config, RESOURCE_ROOT, session, self.stop_event,
                                  lambda message: self.events.put(("log", message)),
                                  lambda kind, data: self.events.put((kind, data)))
            self.events.put(("size", session.size))
            if mode == "run":
                bot.run_continuous() if config["continuous"] else bot.run()
                result = f"本次完成，已确认派出 {bot.completed} 队。"
            else:
                bot.preview()
                result = "预览完成，未点击。绿框表示已识别到的图像。"
            self.events.put(("done", ("success", result)))
        except RunStopped as exc:
            self.events.put(("done", ("stopped", str(exc))))
        except NoIdleQueues as exc:
            self.events.put(("done", ("limited", str(exc))))
        except Exception as exc:
            if self.stop_event.is_set():
                self.events.put(("done", ("stopped", "已按请求停止，不再继续点击。")))
            else:
                self.events.put(("done", ("error", str(exc))))

    def stop(self):
        if self.busy:
            self.stop_event.set()
            self.status_var.set("正在停止")
            self.detail_var.set("停止后不会继续识别或点击。")
            self.add_log("收到停止请求")

    def hotkey_loop(self):
        user32 = ctypes.windll.user32
        user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
        user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
        user32.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT]
        user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        hotkey_id = 0x7701
        message = wintypes.MSG()
        # Create this thread's message queue before exposing its id to close().
        user32.PeekMessageW(ctypes.byref(message), None, 0, 0, 0)
        self.hotkey_thread_id = ctypes.windll.kernel32.GetCurrentThreadId()
        if self.closing.is_set():
            return
        if not user32.RegisterHotKey(None, hotkey_id, 0x4000, win32con.VK_F8):
            self.events.put(("hotkey_unavailable", None))
            return
        self.hotkey_ready.set()
        try:
            while not self.closing.is_set():
                result = user32.GetMessageW(ctypes.byref(message), None, 0, 0)
                if result <= 0:
                    break
                if message.message == win32con.WM_HOTKEY and message.wParam == hotkey_id:
                    self.stop_event.set()
                    self.events.put(("hotkey", None))
        finally:
            user32.UnregisterHotKey(None, hotkey_id)

    def finish(self, result: str, message: str):
        if result == "error" and self.stop_event.is_set():
            self.add_log(message)
            result, message = "stopped", "已按请求停止，不再继续点击。"
        self.set_busy(False)
        statuses = {"success": "预览完成" if self.active_mode == "preview" else "本次完成",
                    "stopped": "已停止", "limited": "没有空闲队列", "error": "已停止 · 请检查"}
        self.status_var.set(statuses[result])
        self.detail_var.set(message)
        self.next_alarm = None
        self.add_log(message)
        if not self.closing.is_set():
            self.root.deiconify()

    def draw_frame(self, frame):
        self.last_image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        self.redraw_image()

    def redraw_image(self):
        if not hasattr(self, "last_image"):
            return
        image = self.last_image.copy()
        image.thumbnail((max(20, self.canvas.winfo_width()), max(20, self.canvas.winfo_height())))
        self.photo = ImageTk.PhotoImage(image)
        self.canvas.delete("all")
        self.canvas.create_image(self.canvas.winfo_width() // 2, self.canvas.winfo_height() // 2, image=self.photo, anchor="center")

    def drain_events(self):
        try:
            while True:
                kind, data = self.events.get_nowait()
                if kind == "log":
                    self.add_log(data)
                elif kind == "frame":
                    self.draw_frame(data)
                elif kind == "size":
                    self.size_var.set(f"{data[0]} × {data[1]}")
                elif kind == "queue":
                    self.idle_var.set(f"{data['idle']} / 3")
                elif kind == "progress":
                    self.progress_var.set(f"{data['completed']} / {data['total']}")
                    self.progress.configure(value=data["completed"])
                elif kind == "occupancy":
                    self.progress_var.set(f"累计 {data['completed']} 次")
                    self.progress.configure(maximum=data["target"], value=min(data["busy"], data["target"]))
                elif kind == "monitor":
                    self.monitor_rows = data["rows"]
                    self.monitor_seen = time.monotonic()
                    delay = data["delay"]
                    self.next_alarm = self.monitor_seen + delay if delay is not None else None
                    if self.busy and self.active_mode == "run":
                        self.status_var.set("正在补队" if delay == 0 else "持续采集 · 监测中")
                        self.detail_var.set("采集结束后继续识别返程；只有空闲才派队。F8 停止。")
                elif kind == "step":
                    self.status_var.set(NAMES.get(data["name"], data["name"]))
                    self.detail_var.set(f"识别置信度 {data['score']:.3f} · F8 停止")
                elif kind == "search_retry":
                    self.next_alarm = time.monotonic() + data["delay"]
                    self.status_var.set("采集点搜索重试")
                    self.detail_var.set("未出现采集按钮，稍后重新检查并搜索。F8 停止。")
                elif kind == "preview":
                    frame = data["frame"].copy()
                    for name, matches in data["matches"].items():
                        for match in matches:
                            cv2.rectangle(frame, (match.x, match.y), (match.x + match.width, match.y + match.height), (135, 230, 95), 2)
                            cv2.putText(frame, f"{name} {match.score:.2f}", (match.x, max(14, match.y - 4)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (135, 230, 95), 1)
                    self.draw_frame(frame)
                elif kind == "diagnostic":
                    frame = data["frame"].copy()
                    match = data["match"]
                    if match:
                        cv2.rectangle(frame, (match.x, match.y),
                                      (match.x + match.width, match.y + match.height), (0, 140, 255), 2)
                    folder = DATA_ROOT / "diagnostics"
                    try:
                        folder.mkdir(parents=True, exist_ok=True)
                        path = folder / f"{time.time_ns()}-{data['name']}.png"
                        ok, buffer = cv2.imencode(".png", frame)
                        if ok:
                            buffer.tofile(str(path))
                            self.add_log(f"识别失败截图：{path}")
                    except OSError as exc:
                        self.add_log(f"保存诊断截图失败：{exc}")
                    self.draw_frame(frame)
                elif kind == "done":
                    self.finish(*data)
                elif kind == "hotkey" and self.busy:
                    self.stop()
                elif kind == "hotkey_unavailable":
                    self.stop_button.configure(text="停止")
                    self.hotkey_label.configure(text="F8 已被其他程序占用\n请使用停止按钮")
                    self.add_log("F8 已被其他程序占用，请使用停止按钮。")
        except queue.Empty:
            pass
        if self.monitor_rows:
            elapsed = int(time.monotonic() - self.monitor_seen) if self.busy else 0
            labels = []
            from queue_monitor import STATE_NAMES
            for row in self.monitor_rows:
                label = f"{row.number}队 {STATE_NAMES[row.state]}"
                if row.seconds is not None:
                    seconds = max(0, row.seconds - elapsed)
                    label += f" {seconds//3600:02}:{seconds//60%60:02}:{seconds%60:02}"
                labels.append(label)
            self.monitor_var.set("  |  ".join(labels))
        if self.next_alarm is not None:
            remaining = max(0, self.next_alarm - time.monotonic())
            alarm_time = time.strftime("%H:%M:%S", time.localtime(time.time() + remaining))
            self.alarm_var.set(f"下次检查 {alarm_time} · {remaining:.0f} 秒后（倒计时为估算，点击前重新识别）")
        elif not self.busy:
            self.alarm_var.set("下一次检查：未运行")
        if not self.closing.is_set():
            self.root.after(100, self.drain_events)

    def close(self):
        self.closing.set()
        self.stop_event.set()
        if self.hotkey_thread_id:
            ctypes.windll.user32.PostThreadMessageW(self.hotkey_thread_id, win32con.WM_QUIT, 0, 0)
        if self.countdown_id:
            self.root.after_cancel(self.countdown_id)
            self.countdown_id = None
        self.status_var.set("正在退出")
        self.wait_for_close()

    def wait_for_close(self):
        if self.worker and self.worker.is_alive():
            self.root.after(100, self.wait_for_close)
        else:
            self.root.destroy()


def main():
    root = tk.Tk()
    app = HelperApp(root)
    if "--smoke-test" in sys.argv:
        output = Path(sys.argv[sys.argv.index("--smoke-test") + 1])
        def smoke():
            try:
                config = read_gather_config(RESOURCE_ROOT / "gather_crystal.yaml")
                assert app.hotkey_ready.is_set(), "F8 hotkey registration failed"
                app.set_busy(True)
                app.stop_event.clear()
                app.count_down(5, "preview", config)
                app.stop()
                app.count_down(0, "preview", config)
                assert not app.busy and app.worker is None
                app.finish("error", "测试前台切换与停止同时发生")
                assert app.status_var.get() == "已停止"
                app.stop_event.clear()
                ctypes.windll.user32.PostThreadMessageW(app.hotkey_thread_id, win32con.WM_HOTKEY, 0x7701, 0)
                assert app.stop_event.wait(0.5), "Hotkey message did not stop the worker"
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text(json.dumps({"ok": True, "templates": len(config["templates"]),
                                              "frozen": bool(getattr(sys, "frozen", False)),
                                              "tk": root.tk.call("info", "patchlevel"),
                                              "cancel_before_click": True,
                                              "stop_focus_race": True,
                                              "hotkey_registered": True,
                                              "hotkey_stop": True}), encoding="utf-8")
            finally:
                app.close()
        root.after(800, smoke)
    root.mainloop()


if __name__ == "__main__":
    main()
