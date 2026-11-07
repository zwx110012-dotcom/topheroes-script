"""Exercise the real Tk layout without capturing or clicking the game."""
import time
from queue_monitor import QueueRow


def check_layout(app):
    root = app.root
    original = root.geometry()
    capacity = app.capacity_var.get()
    detail = app.detail_var.get()
    def inside(widget):
        assert widget.winfo_viewable(), f'{widget} hidden'
        assert widget.winfo_width() > 5 and widget.winfo_height() > 5
        assert widget.winfo_rootx() >= root.winfo_rootx()
        assert widget.winfo_rooty() >= root.winfo_rooty()
        assert widget.winfo_rootx() + widget.winfo_width() <= root.winfo_rootx() + root.winfo_width(), (str(widget), widget.winfo_geometry(), root.geometry(), widget.winfo_rootx(), root.winfo_rootx())
        assert widget.winfo_rooty() + widget.winfo_height() <= root.winfo_rooty() + root.winfo_height(), (str(widget), widget.winfo_geometry(), root.geometry(), widget.winfo_rooty(), root.winfo_rooty())
    try:
        for geometry in ('1160x800', '1000x700'):
            root.geometry(geometry)
            root.update()
            app.detail_var.set('窗口状态需要重新确认，识别失败后将恢复世界页面并重新检查队列。' * 3)
            for settings_page in (0, 1):
                app.settings_tabs.select(settings_page)
                for view_page in (0, 1):
                    app.view_tabs.select(view_page)
                    root.update()
                    for widget in (app.start_button, app.stop_button, app.launch_button, app.hotkey_label):
                        inside(widget)
                    if settings_page == 0:
                        for widget in (app.level_combo, app.capacity_spin, app.count_spin, app.chat_check, app.delay_spin):
                            inside(widget)
                    else:
                        for widget in (app.window_combo, app.model_entry, app.model_button):
                            inside(widget)
                    if view_page == 0:
                        inside(app.canvas)
                        assert app.canvas.winfo_height() >= 50, 'Preview squeezed away'
                        for widget in (app.preview_button, app.image_button, app.capture_button):
                            inside(widget)
                    else:
                        inside(app.log_text)
        app.capacity_var.set('4')
        app.monitor_rows = [QueueRow(1, 'mining', 120), QueueRow(2, 'returning', 15),
                            QueueRow(3, 'idle'), QueueRow(4, 'outbound', 30)]
        app.monitor_seen = time.monotonic() - 10
        app.set_busy(True)
        app.refresh_queue_cards()
        assert app.queue_times[0].cget('text') == '00:01:50'
        assert '返程中' in app.queue_labels[1].cget('text')
        assert '空闲' in app.queue_labels[2].cget('text')
        assert str(app.level_combo.cget('state')) == 'disabled'
        assert str(app.chat_check.cget('state')) == 'disabled'
        assert str(app.stop_button.cget('state')) == 'normal'
        assert str(app.launch_button.cget('state')) == 'disabled'
        app.monitor_paused_at = app.monitor_seen + 10
        app.set_busy(False)
        app.refresh_queue_cards()
        assert app.queue_times[0].cget('text') == '00:01:50', 'Stopped timers must not jump backwards'
        app.capacity_var.set('3')
        app.refresh_queue_cards()
        assert '未启用' in app.queue_labels[3].cget('text')
        return {'sizes': ['1160x800', '1000x700'], 'all_tabs_fit': True,
                'queue_states': True, 'stopped_timer_frozen': True, 'busy_lock': True}
    finally:
        app.set_busy(False)
        app.capacity_var.set(capacity)
        app.detail_var.set(detail)
        app.monitor_rows = []
        app.monitor_paused_at = None
        app.refresh_queue_cards()
        app.settings_tabs.select(0)
        app.view_tabs.select(0)
        root.geometry(original)
        root.update_idletasks()
