# UI layout verification — 2026-10-18

- Presentation moved to `helper_ui.py`; task settings and connection/model settings have separate tabs.
- Runtime dashboard has four queue cards, an absolute alarm readout, preview/log tabs and fixed bottom controls.
- `ui_smoke.check_layout()` runs as part of both source and packaged `--smoke-test`.
- At 1160×800 and 1000×700, each settings/view tab combination keeps its controls and start/stop inside the root; preview retains usable height. Long status detail is included in this check.
- Simulated mining/returning/idle/outbound states, three-queue mode, disabled settings during a run and frozen stopped timers pass.
- Source smoke and packaged smoke pass: F8 registration/interruption, cancellation before clicks, level selector, default-off carriage monitoring and toggle.
- `python -X utf8 -m unittest discover -s tests`: 104 tests passed.
- Actual packaged default window visually inspected through computer-use; screenshot saved as `yolo-output/ui-redesign-desktop.png`.
- EXE overwritten at `dist/TopHeroesHelper-YOLO.exe`. No game actions were started in this UI verification; a full mining/carriage cycle was not rerun.
- Different Windows DPI settings and a second computer have not been tested in this change.

Evidence: `yolo-output/ui-redesign-source-smoke.json`, `ui-redesign-exe-smoke.json`, `tests-ui-redesign.log`, `build-ui-redesign.log`.
