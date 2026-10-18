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
from tkinter import ttk, filedialog

import cv2
import win32con
import win32gui
from PIL import Image, ImageTk

from crystal_gather import CrystalGatherer, GameSession, NoIdleQueues, RunStopped, read_gather_config, available_levels
from top_heroes import WorkflowError, client_box, find_game_window as find_window, game_windows


RESOURCE_ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
DATA_ROOT = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "TopHeroesHelper"
from helper_ui import BG, PANEL, FIELD, TEXT, MUTED, ACCENT
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
        model_root = Path(sys.executable).parent if getattr(sys, "frozen", False) else RESOURCE_ROOT
        self.recognition_var = tk.StringVar(value=self.settings.get("recognition", "yolo"))
        self.model_var = tk.StringVar(value=self.settings.get("yolo_model", str(model_root / "models/topheroes.onnx")))
        DATA_ROOT.mkdir(parents=True, exist_ok=True)
        self.log_path = DATA_ROOT / "logs" / (datetime.now().strftime("%Y%m%d-%H%M%S") + ".log")
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.title_var = tk.StringVar(value=self.settings.get("title", "Steam Top Heroes"))
        self.capacity_var = tk.StringVar(value=str(self.settings.get('queue_capacity',4)))
        self.count_var = tk.StringVar(value=str(self.settings.get('dispatches',4)
            if 'queue_capacity' in self.settings else 4))
        self.delay_var = tk.StringVar(value=str(self.settings.get("delay", 5)))
        self.continuous_var = tk.BooleanVar(value=self.settings.get("continuous", True))
        # Carriage monitoring is opt-in on every launch, including upgrades
        # from versions that saved chat_invites=True.
        self.chat_var = tk.BooleanVar(value=False)
        self.levels=available_levels(RESOURCE_ROOT)
        saved_level=self.settings.get('target_level',7)
        self.level_var=tk.StringVar(value=str(saved_level if saved_level in self.levels else 7))
        self.monitor_var = tk.StringVar(value="队伍倒计时：等待识别")
        self.alarm_var = tk.StringVar(value="下一次检查：—")
        self.next_alarm = None
        self.monitor_rows = []
        self.monitor_seen = 0.0
        self.monitor_paused_at = None
        self.status_var = tk.StringVar(value="等待开始")
        self.detail_var = tk.StringVar(value="启动时恢复游戏画面为1600×900，再识别并采集。")
        self.size_var = tk.StringVar(value="未连接")
        self.idle_var = tk.StringVar(value="—")
        self.progress_var = tk.StringVar(value=f"0 / {self.count_var.get()}")
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
        from helper_ui import build_ui
        build_ui(self, RESOURCE_ROOT)

    def open_logs(self):
        os.startfile(self.log_path.parent)

    def add_log(self, message: str):
        line = f"[{time.strftime('%H:%M:%S')}] {message}"
        self.lines.append(line)
        self.log_text.configure(state="normal")
        tag = 'stop' if '停止' in message else 'warning' if any(word in message for word in ('失败', '超时', '错误', '重试')) else ''
        self.log_text.insert("end", line + "\n", tag)
        self.last_log_var.set(line)
        # Keep the visible log bounded; the complete log stays on disk.
        if len(self.lines) > 1500:
            self.lines = self.lines[-1000:]
            self.log_text.delete('1.0', '501.0')
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
        if busy:
            self.monitor_paused_at = None
        for button in (self.start_button, self.preview_button, self.refresh_button,
                       self.capture_button, self.image_button, self.model_button):
            button.configure(state="disabled" if busy else "normal")
        self.stop_button.configure(state="normal" if busy else "disabled")
        for widget in (self.window_combo, self.capacity_spin, self.count_spin, self.delay_spin, self.recognition_combo,self.level_combo):
            widget.configure(state="disabled" if busy else "readonly")
        self.continuous_check.configure(state="disabled" if busy else "normal")
        self.chat_check.configure(state='disabled' if busy else 'normal')
        self.model_entry.configure(state="disabled" if busy else "normal")

    def choose_model(self):
        path = filedialog.askopenfilename(title="选择游戏检测 ONNX 模型", filetypes=[("ONNX 模型", "*.onnx")])
        if path:
            self.model_var.set(path)
            self.recognition_var.set("yolo")

    def open_captures(self):
        folder = DATA_ROOT / "captures"
        folder.mkdir(parents=True, exist_ok=True)
        os.startfile(folder)

    def recognition_config(self, config: dict):
        config.update(recognition=self.recognition_var.get(), yolo_model=self.model_var.get().strip(),
                      yolo_confidence=0.65)
        if config["recognition"] not in ("yolo", "template"):
            raise WorkflowError("请选择 yolo 或 template")
        if config["recognition"] == "yolo" and not Path(config["yolo_model"]).is_file():
            raise WorkflowError("YOLO 模型待训练；请先保存训练截图，或选择已训练的 ONNX 模型")

    def preview_image(self):
        if self.busy:
            return
        path = filedialog.askopenfilename(title="选择游戏截图", filetypes=[("游戏截图", "*.png *.jpg *.jpeg")])
        if not path:
            return
        try:
            config = read_gather_config(RESOURCE_ROOT / "gather_crystal.yaml")
            self.recognition_config(config)
            config['queue_capacity'] = int(self.capacity_var.get())
            config['target_level']=int(self.level_var.get())
        except Exception as exc:
            self.status_var.set("无法预览")
            self.detail_var.set(str(exc))
            return
        self.stop_event.clear()
        self.active_mode = "preview"
        self.set_busy(True)
        self.status_var.set("正在识别截图")
        def work():
            try:
                from top_heroes import load_template
                frame = load_template(Path(path))
                class ImageSession:
                    def capture(self):
                        return frame
                    def click(self, point):
                        raise WorkflowError("截图预览不能点击")
                bot = CrystalGatherer(config, RESOURCE_ROOT, ImageSession(), self.stop_event,
                    lambda msg: self.events.put(("log", msg)),
                    lambda kind, data: self.events.put((kind, data)))
                bot.preview()
                self.events.put(("done", ("success", "截图预览完成；红十字为预计点击位置，未操作游戏")))
            except Exception as exc:
                self.events.put(("done", ("error", str(exc))))
        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def start(self, mode: str):
        if self.busy:
            return
        try:
            config = read_gather_config(RESOURCE_ROOT / "gather_crystal.yaml")
            title, count, delay = self.title_var.get().strip(), int(self.count_var.get()), int(self.delay_var.get())
            capacity = int(self.capacity_var.get())
            if not title or capacity not in (3,4) or not 1 <= count <= capacity or not 2 <= delay <= 15:
                raise ValueError("请选择3或4个队列、1到队列总数的目标，以及2到15秒倒计时")
            find_window(title)
            continuous = self.continuous_var.get()
            config.update(window_title=title, dispatches=count, continuous=continuous,queue_capacity=capacity)
            level=int(self.level_var.get())
            if level not in self.levels:raise WorkflowError('请选择有效矿点等级')
            config['target_level']=level
            config['chat_invites']=self.chat_var.get()
            if mode=='run' and continuous and config['chat_invites'] and capacity==4:
                from chat_invites import ChatImages
                ChatImages(RESOURCE_ROOT)
            if mode != "capture":
                self.recognition_config(config)
            if mode == "run" and config.get("recognition") == "yolo":
                from yolo_detector import require_verified_model
                require_verified_model(Path(config["yolo_model"]),capacity=capacity)
            (DATA_ROOT / "settings.json").write_text(json.dumps({"title": title, "dispatches": count,
                "queue_capacity":capacity,
                "target_level":level,
                "chat_invites":config['chat_invites'],
                "delay": delay, "continuous": continuous, "recognition": self.recognition_var.get(),
                "yolo_model": self.model_var.get()}, ensure_ascii=False), encoding="utf-8")
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
        self.add_log(f"开始采集：{level}级红水晶" if mode == "run" else "保存训练截图" if mode == "capture" else "开始识别预览，不会点击")
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
            if mode == "capture":
                frame = session.capture()
                folder = DATA_ROOT / "captures"
                folder.mkdir(parents=True, exist_ok=True)
                path = folder / f"game-{datetime.now():%Y%m%d-%H%M%S}-{time.time_ns()}.png"
                ok, buffer = cv2.imencode(".png", frame)
                if not ok:
                    raise WorkflowError("截图编码失败")
                buffer.tofile(str(path))
                self.events.put(("frame", frame))
                self.events.put(("done", ("success", f"训练截图已保存：{path}")))
                return
            bot = CrystalGatherer(config, RESOURCE_ROOT, session, self.stop_event,
                                  lambda message: self.events.put(("log", message)),
                                  lambda kind, data: self.events.put((kind, data)))
            self.events.put(("size", session.size))
            if mode == "run":
                bot.require_ready()
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
        self.monitor_paused_at = time.monotonic()
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
            self.canvas.delete('all')
            self.canvas.create_text(self.canvas.winfo_width() // 2, self.canvas.winfo_height() // 2,
                text='等待游戏画面\n启动后显示识别结果，也可打开截图预览', fill=MUTED,
                font=('Microsoft YaHei UI', 11), justify='center', tags='placeholder')
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
                    self.idle_var.set(f"{data['idle']} / {data.get('capacity',self.capacity_var.get())}")
                elif kind == "progress":
                    self.progress_var.set(f"{data['completed']} / {data['total']}")
                    self.progress.configure(value=data["completed"])
                elif kind == "occupancy":
                    self.progress_var.set(f"累计 {data['completed']} 次")
                    self.progress.configure(maximum=data["target"], value=min(data["busy"], data["target"]))
                elif kind == "monitor":
                    self.monitor_rows = data["rows"]
                    now=time.monotonic()
                    self.monitor_seen = data.get('observed_at',now)
                    delay = data["delay"]
                    self.next_alarm = data.get('deadline',now+delay if delay is not None else None)
                    if self.busy and self.active_mode == "run":
                        self.status_var.set("正在补队" if delay == 0 else "持续采集 · 监测中")
                        self.detail_var.set("采集结束后继续识别返程；只有空闲才派队。F8 停止。")
                elif kind == "step":
                    self.status_var.set(NAMES.get(data["name"], data["name"]))
                    self.detail_var.set(f"识别置信度 {data['score']:.3f} · F8 停止")
                elif kind=='chat_watch':
                    self.status_var.set('四队采矿 · 聊天邀请监测')
                    self.detail_var.set(f"公会商队：已确认加入 {data['joined']} 次；F8停止。")
                elif kind=='chat_alarm':
                    self.next_alarm=data['deadline']
                    self.status_var.set('四队采矿 · 实时监测商队')
                    self.detail_var.set(f"按{data['queue']}队最短采集倒计时设闹钟；到时退出、等待返程并补队。F8停止。")
                elif kind == "search_retry":
                    self.next_alarm = time.monotonic() + data["delay"]
                    self.status_var.set("采集点搜索重试")
                    self.detail_var.set("未出现采集按钮，稍后重新检查并搜索。F8 停止。")
                elif kind == "queue_wait":
                    self.monitor_rows = []
                    self.idle_var.set("—")
                    self.next_alarm = time.monotonic() + data["delay"]
                    self.status_var.set("队伍栏待确认 · 等待复查")
                    self.detail_var.set("暂停点击，重新确认队伍状态后继续。F8 停止。")
                elif kind == "preview":
                    frame = data["frame"].copy()
                    for name, matches in data["matches"].items():
                        for match in matches:
                            cv2.rectangle(frame, (match.x, match.y), (match.x + match.width, match.y + match.height), (135, 230, 95), 2)
                            cv2.putText(frame, f"{name} {match.score:.2f}", (match.x, max(14, match.y - 4)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (135, 230, 95), 1)
                            cv2.drawMarker(frame, match.center, (40, 40, 255), cv2.MARKER_CROSS, 18, 2)
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
            elapsed = self.queue_elapsed()
            labels = []
            from queue_monitor import STATE_NAMES
            for row in self.monitor_rows:
                label = f"{row.number}队 {STATE_NAMES[row.state]}"
                if row.seconds is not None:
                    seconds = max(0, row.seconds - elapsed)
                    label += f" {seconds//3600:02}:{seconds//60%60:02}:{seconds%60:02}"
                labels.append(label)
            self.monitor_var.set("  |  ".join(labels))
        self.refresh_queue_cards()
        if self.next_alarm is not None:
            remaining = max(0, self.next_alarm - time.monotonic())
            alarm_time = time.strftime("%H:%M:%S", time.localtime(time.time() + remaining))
            self.alarm_var.set(f"下次检查 {alarm_time} · {remaining:.0f} 秒后（倒计时为估算，点击前重新识别）")
        elif not self.busy:
            self.alarm_var.set("下一次检查：未运行")
        if not self.closing.is_set():
            self.root.after(100, self.drain_events)

    def queue_elapsed(self):
        now = time.monotonic() if self.busy else self.monitor_paused_at or self.monitor_seen
        return max(0, int(now - self.monitor_seen))

    def refresh_queue_cards(self):
        from queue_monitor import STATE_NAMES
        rows = {row.number: row for row in self.monitor_rows}
        elapsed = self.queue_elapsed()
        colors = {'mining': ACCENT, 'returning': '#f3bd76', 'idle': '#9cbcff'}
        for number, (label, timer) in enumerate(zip(self.queue_labels, self.queue_times), 1):
            row = rows.get(number)
            if number > int(self.capacity_var.get()):
                label.configure(text=f'{number} 队 · 未启用', fg=MUTED)
                timer.configure(text='—', fg=MUTED)
            elif row is None:
                label.configure(text=f'{number} 队 · 待识别', fg=MUTED)
                timer.configure(text='—', fg=MUTED)
            else:
                label.configure(text=f'{number} 队 · {STATE_NAMES[row.state]}', fg=colors.get(row.state, MUTED))
                seconds = max(0, row.seconds - elapsed) if row.seconds is not None else None
                value = f'{seconds//3600:02}:{seconds//60%60:02}:{seconds%60:02}' if seconds is not None else '—'
                timer.configure(text=value, fg=TEXT)

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
    if "--yolo-smoke-test" in sys.argv:
        index = sys.argv.index("--yolo-smoke-test")
        model, source, output = map(Path, sys.argv[index + 1:index + 4])
        def yolo_smoke():
            try:
                import numpy as np
                from yolo_detector import YoloDetector
                frame = cv2.imdecode(np.fromfile(str(source), np.uint8), cv2.IMREAD_COLOR)
                if frame is None:
                    raise WorkflowError("测试截图无法读取")
                detector = YoloDetector(model, 0.25)
                detections = detector.detect(frame)
                visible = {}
                for d in detections:
                    visible.setdefault(d.name, []).append(d.match)
                app.events.put(("preview", {"frame": frame, "matches": visible}))
                app.status_var.set("YOLO 截图验证完成")
                app.detail_var.set("绿框：检测目标；红十字：客户区点击点。当前模型仅可预览。")
                app.recognition_var.set("yolo")
                app.model_var.set(str(model))
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text(json.dumps({"ok": True, "frozen": bool(getattr(sys, "frozen", False)),
                    "providers": detector.session.get_providers(), "classes": detector.names,
                    "boxes": [{"name": d.name, "score": d.match.score,
                               "center": d.match.center} for d in detections], "clicked": False},
                    ensure_ascii=False, indent=2), encoding="utf-8")
                root.after(60000 if "--hold-open" in sys.argv else 1000, app.close)
            except Exception as exc:
                output.write_text(json.dumps({"ok": False, "error": str(exc)}), encoding="utf-8")
                app.status_var.set("YOLO 验证失败")
                app.detail_var.set(str(exc))
                root.after(1000, app.close)
        root.after(800, yolo_smoke)
    if "--smoke-test" in sys.argv:
        output = Path(sys.argv[sys.argv.index("--smoke-test") + 1])
        def smoke():
            try:
                config = read_gather_config(RESOURCE_ROOT / "gather_crystal.yaml")
                from ui_smoke import check_layout
                layout_checks = check_layout(app)
                assert app.hotkey_ready.is_set(), "F8 hotkey registration failed"
                root.update_idletasks()
                assert app.level_combo.winfo_viewable(), "Level selector is not visible"
                assert not app.chat_var.get(), "Carriage monitoring must start unchecked"
                assert app.chat_check.cget('text')=='马车监测', "Carriage checkbox label is wrong"
                app.chat_check.invoke()
                assert app.chat_var.get(), "Carriage monitoring cannot be enabled"
                app.chat_check.invoke()
                assert not app.chat_var.get(), "Carriage monitoring cannot be disabled"
                assert (app.stop_button.winfo_rooty()-root.winfo_rooty()+app.stop_button.winfo_height()
                        <=root.winfo_height()), "Stop button is outside the window"
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
                                              "hotkey_stop": True,
                                              "target_level":int(app.level_var.get()),
                                              "available_levels":app.levels,
                                              "level_selector_visible":True,
                                              "stop_button_visible":True,
                                              "carriage_monitor_default":False,
                                              "carriage_monitor_toggle":True,
                                              "layout":layout_checks}), encoding="utf-8")
            finally:
                app.close()
        root.after(800, smoke)
    root.mainloop()


if __name__ == "__main__":
    main()
