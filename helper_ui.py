"""Presentation for the desktop helper; automation lives in the controller."""
import tkinter as tk
from tkinter import ttk
from PIL import Image, ImageTk

BG = '#10141e'
PANEL = '#191f2c'
FIELD = '#242e40'
TEXT = '#eef2fa'
MUTED = '#9caac0'
ACCENT = '#7de2c3'


def build_ui(app, resource_root):
    root = app.root
    root.title('Top Heroes · 日常助手')
    root.geometry('1160x800')
    root.minsize(1000, 700)
    root.configure(bg=BG)
    root.iconphoto(True, ImageTk.PhotoImage(Image.open(resource_root / 'templates/world.png')))
    style = ttk.Style(root)
    style.theme_use('clam')
    style.configure('.', font=('Microsoft YaHei UI', 10))
    for name in ('TCombobox', 'TSpinbox', 'TEntry'):
        style.configure(name, fieldbackground=FIELD, background=FIELD, foreground=TEXT,
                        arrowcolor=TEXT, bordercolor=FIELD, padding=6, insertcolor=TEXT)
        style.map(name, fieldbackground=[('disabled', PANEL), ('readonly', FIELD)],
                  foreground=[('disabled', MUTED), ('readonly', TEXT)])
    style.configure('TNotebook', background=PANEL, borderwidth=0, tabmargins=0,
                    bordercolor=PANEL, lightcolor=PANEL, darkcolor=PANEL)
    style.configure('TNotebook.Tab', background=PANEL, foreground=MUTED, padding=(16, 9), borderwidth=0,
                    bordercolor=PANEL, lightcolor=PANEL, darkcolor=PANEL)
    style.map('TNotebook.Tab', background=[('selected', FIELD)], foreground=[('selected', ACCENT)])
    style.configure('Horizontal.TProgressbar', background=ACCENT, troughcolor=FIELD,
                    borderwidth=0, thickness=4, bordercolor=FIELD, lightcolor=FIELD, darkcolor=FIELD)
    style.layout('Horizontal.TProgressbar', [('Horizontal.Progressbar.trough', {'sticky': 'nswe',
        'children': [('Horizontal.Progressbar.pbar', {'side': 'left', 'sticky': 'ns'})]})])
    root.option_add('*TCombobox*Listbox.background', FIELD)
    root.option_add('*TCombobox*Listbox.foreground', TEXT)
    root.option_add('*TCombobox*Listbox.selectBackground', '#365a68')
    root.grid_columnconfigure(0, weight=1)
    root.grid_rowconfigure(1, weight=1)

    def label(parent, text=None, variable=None, size=10, color=TEXT, bold=False, bg=PANEL):
        return app.label(parent, text, variable, size, color, bg, bold)

    def note(parent, text):
        widget = label(parent, text, size=9, color=MUTED)
        widget.configure(wraplength=250, justify='left')
        widget.pack(fill='x', pady=(4, 12))
        return widget

    def field(parent, title, variable, values=None):
        row = tk.Frame(parent, bg=PANEL)
        row.pack(fill='x', pady=5)
        label(row, title).pack(side='left')
        widget = ttk.Combobox(row, textvariable=variable, values=values, width=6, state='readonly')
        widget.pack(side='right')
        return widget

    def divider(parent):
        tk.Frame(parent, bg=FIELD, height=1).pack(fill='x', pady=12)

    header = tk.Frame(root, bg=BG, padx=24, pady=16)
    header.grid(row=0, column=0, sticky='ew')
    label(header, 'TOP HEROES', size=18, bold=True, bg=BG).pack(side='left')
    label(header, ' /  日常助手', size=12, color=MUTED, bg=BG).pack(side='left', padx=12)
    label(header, '视觉识别  ·  自动采集', color=ACCENT, bg=BG).pack(side='right')

    body = tk.Frame(root, bg=BG)
    body.grid(row=1, column=0, sticky='nsew', padx=20)
    body.grid_columnconfigure(1, weight=1)
    body.grid_rowconfigure(0, weight=1)
    side = tk.Frame(body, bg=PANEL, width=292)
    side.grid(row=0, column=0, sticky='nsew', padx=(0, 16))
    side.grid_propagate(False)
    side.grid_rowconfigure(0, weight=1)
    side.grid_columnconfigure(0, weight=1)
    app.settings_tabs = ttk.Notebook(side)
    app.settings_tabs.grid(row=0, column=0, sticky='nsew')
    task = tk.Frame(app.settings_tabs, bg=PANEL, padx=18, pady=14)
    advanced = tk.Frame(app.settings_tabs, bg=PANEL, padx=18, pady=14)
    app.settings_tabs.add(task, text='任务配置')
    app.settings_tabs.add(advanced, text='连接与识别')

    label(task, '01  红水晶采集', size=14, bold=True).pack(anchor='w', pady=(0, 8))
    app.level_combo = field(task, '矿点等级', app.level_var, app.levels)
    app.capacity_spin = field(task, '队列总数', app.capacity_var, (3, 4))
    app.count_spin = field(task, '目标在外队伍', app.count_var, (1, 2, 3, 4))
    app.continuous_check = tk.Checkbutton(task, text='持续采集', variable=app.continuous_var,
        bg=PANEL, fg=TEXT, selectcolor=FIELD, activebackground=PANEL, activeforeground=TEXT,
        font=('Microsoft YaHei UI', 10), anchor='w', padx=0)
    app.continuous_check.pack(fill='x', pady=(8, 0))
    note(task, '等待队伍自然返程，确认空闲后补队。')
    divider(task)
    label(task, '02  商队邀请', size=14, bold=True).pack(anchor='w', pady=(0, 8))
    app.chat_check = tk.Checkbutton(task, text='马车监测', variable=app.chat_var,
        bg=PANEL, fg=TEXT, selectcolor=FIELD, activebackground=PANEL, activeforeground=TEXT,
        font=('Microsoft YaHei UI', 10), anchor='w', padx=0)
    app.chat_check.pack(fill='x')
    note(task, '默认关闭。需持续采集、四队采矿且有剩余次数；按最短采集闹钟返回补队。')
    divider(task)
    app.delay_spin = field(task, '启动等待 / 秒', app.delay_var, tuple(range(2, 16)))
    note(task, '启动后自动切换游戏，恢复 1600 × 900。')

    label(advanced, '游戏窗口', size=12, bold=True).pack(anchor='w')
    app.window_combo = ttk.Combobox(advanced, textvariable=app.title_var, state='readonly', width=18)
    app.window_combo.pack(fill='x', pady=(10, 8))
    app.window_combo.bind('<<ComboboxSelected>>', lambda event: app.refresh_windows())
    app.refresh_button = app.button(advanced, '刷新窗口', app.refresh_windows)
    app.refresh_button.pack(fill='x')
    divider(advanced)
    label(advanced, '识别方式', size=12, bold=True).pack(anchor='w')
    app.recognition_combo = ttk.Combobox(advanced, textvariable=app.recognition_var,
                                        values=('yolo', 'template'), state='readonly')
    app.recognition_combo.pack(fill='x', pady=10)
    label(advanced, 'ONNX 模型', color=MUTED, size=9).pack(anchor='w')
    app.model_entry = ttk.Entry(advanced, textvariable=app.model_var)
    app.model_entry.pack(fill='x', pady=(6, 8))
    app.model_button = app.button(advanced, '选择模型文件', app.choose_model)
    app.model_button.pack(fill='x')
    note(advanced, '使用随程序提供的模型；通常无需修改。')

    main = tk.Frame(body, bg=BG)
    main.grid(row=0, column=1, sticky='nsew')
    main.grid_columnconfigure(0, weight=1)
    main.grid_rowconfigure(4, weight=1)
    status = tk.Frame(main, bg=PANEL, padx=18, pady=12)
    status.grid(row=0, column=0, sticky='ew', pady=(0, 12))
    label(status, '运行状态', color=MUTED, size=9).pack(anchor='w')
    label(status, variable=app.status_var, size=20, bold=True).pack(anchor='w', pady=(3, 2))
    detail = label(status, variable=app.detail_var, color=MUTED, size=9)
    detail.configure(justify='left', wraplength=650, height=2)
    detail.pack(fill='x')
    status.bind('<Configure>', lambda e: detail.configure(wraplength=max(100, e.width - 36)))

    stats = tk.Frame(main, bg=BG)
    stats.grid(row=1, column=0, sticky='ew', pady=(0, 12))
    for index, (name, variable) in enumerate((('游戏画面', app.size_var), ('空闲队列', app.idle_var), ('本次派队', app.progress_var))):
        stats.grid_columnconfigure(index, weight=1, uniform='stats')
        card = tk.Frame(stats, bg=PANEL, padx=14, pady=8)
        card.grid(row=0, column=index, sticky='nsew', padx=(0, 8 if index < 2 else 0))
        label(card, name, color=MUTED, size=9).pack(anchor='w')
        label(card, variable=variable, size=15, bold=True).pack(anchor='w', pady=(3, 0))

    queues = tk.Frame(main, bg=BG)
    queues.grid(row=2, column=0, sticky='ew', pady=(0, 8))
    app.queue_labels = []
    app.queue_times = []
    for index in range(4):
        queues.grid_columnconfigure(index, weight=1, uniform='queues')
        card = tk.Frame(queues, bg=PANEL, padx=12, pady=8)
        card.grid(row=0, column=index, sticky='nsew', padx=(0, 8 if index < 3 else 0))
        state = label(card, f'{index + 1} 队 · 待识别', color=MUTED, size=9)
        state.pack(anchor='w')
        timer = label(card, '—', size=15, bold=True)
        timer.configure(font=('Consolas', 16, 'bold'))
        timer.pack(anchor='w', pady=(3, 0))
        app.queue_labels.append(state)
        app.queue_times.append(timer)

    alarm = label(main, variable=app.alarm_var, color=ACCENT, size=9, bg=BG)
    alarm.configure(wraplength=680, justify='left')
    alarm.grid(row=3, column=0, sticky='ew', pady=(0, 10))
    alarm.bind('<Configure>', lambda e: alarm.configure(wraplength=max(100, e.width)))
    app.view_tabs = ttk.Notebook(main)
    app.view_tabs.grid(row=4, column=0, sticky='nsew')
    preview = tk.Frame(app.view_tabs, bg=PANEL)
    logs = tk.Frame(app.view_tabs, bg=PANEL)
    app.view_tabs.add(preview, text='识别画面')
    app.view_tabs.add(logs, text='运行日志')
    tools = tk.Frame(preview, bg=PANEL, padx=8, pady=6)
    tools.pack(side='bottom', fill='x')
    app.preview_button = app.button(tools, '识别游戏', lambda: app.start('preview'))
    app.image_button = app.button(tools, '打开截图', app.preview_image)
    app.capture_button = app.button(tools, '保存游戏截图', lambda: app.start('capture'))
    for button in (app.preview_button, app.image_button, app.capture_button):
        button.configure(font=('Microsoft YaHei UI', 9), padx=10, pady=5)
        button.pack(side='left', padx=(0, 6))
    app.canvas = tk.Canvas(preview, bg='#0c1018', highlightthickness=0, height=160)
    app.canvas.pack(fill='both', expand=True)
    app.canvas.bind('<Configure>', lambda event: app.redraw_image())
    app.log_text = tk.Text(logs, height=4, bg=PANEL, fg=TEXT, insertbackground=TEXT,
        font=('Microsoft YaHei UI', 10), borderwidth=0, padx=14, pady=12, wrap='word', state='disabled')
    scrollbar = ttk.Scrollbar(logs, command=app.log_text.yview)
    app.log_text.configure(yscrollcommand=scrollbar.set)
    app.log_text.pack(side='left', fill='both', expand=True)
    scrollbar.pack(side='right', fill='y')
    app.log_text.tag_configure('warning', foreground='#f3bd76')
    app.log_text.tag_configure('stop', foreground='#ff9b9b')
    app.progress = ttk.Progressbar(main, maximum=app.count_var.get(), mode='determinate')
    app.progress.grid(row=5, column=0, sticky='ew', pady=(10, 6))
    app.last_log_var = tk.StringVar(value='等待运行')
    last = label(main, variable=app.last_log_var, color=MUTED, size=9, bg=BG)
    last.configure(height=1)
    last.grid(row=6, column=0, sticky='ew')

    footer = tk.Frame(root, bg=PANEL, padx=20, pady=12)
    footer.grid(row=2, column=0, sticky='ew', pady=(16, 0))
    app.start_button = app.button(footer, '启动采集', lambda: app.start('run'), True)
    app.start_button.pack(side='right', padx=(10, 0))
    app.stop_button = app.button(footer, '停止  F8', app.stop)
    app.stop_button.configure(fg='#ffb2b2', state='disabled')
    app.stop_button.pack(side='right', padx=(16, 0))
    app.hotkey_label = label(footer, 'F8 随时停止 · 切换离开游戏会停止', size=9, color=MUTED)
    app.hotkey_label.pack(side='left')
    app.button(footer, '日志目录', app.open_logs).pack(side='left', padx=(16, 6))
    app.button(footer, '截图目录', app.open_captures).pack(side='left')
