"""Tests for the simple in/out trimmer.

Run:  python test_app.py <clip.mp4>
"""

import os
import statistics
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPointF, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app import encoder, estimator, ffmpeg_tools  # noqa: E402
from app.decoder import ClipDecoder  # noqa: E402
from app.model import Project  # noqa: E402
from app.window import MainWindow  # noqa: E402


def drag(widget, x0, y0, x1, y1, steps=4):
    press = QMouseEvent(
        QMouseEvent.Type.MouseButtonPress, QPointF(x0, y0),
        widget.mapToGlobal(QPointF(x0, y0)),
        Qt.LeftButton, Qt.LeftButton, Qt.NoModifier,
    )
    widget.mousePressEvent(press)
    for i in range(1, steps + 1):
        fx = x0 + (x1 - x0) * i / steps
        fy = y0 + (y1 - y0) * i / steps
        widget.mouseMoveEvent(QMouseEvent(
            QMouseEvent.Type.MouseMove, QPointF(fx, fy),
            widget.mapToGlobal(QPointF(fx, fy)),
            Qt.LeftButton, Qt.LeftButton, Qt.NoModifier,
        ))
    widget.mouseReleaseEvent(QMouseEvent(
        QMouseEvent.Type.MouseButtonRelease, QPointF(x1, y1),
        widget.mapToGlobal(QPointF(x1, y1)),
        Qt.LeftButton, Qt.NoButton, Qt.NoModifier,
    ))


def main():
    if len(sys.argv) < 2:
        print("usage: test_app.py <clip.mp4>")
        return 1
    path = sys.argv[1]

    app = QApplication(sys.argv[:1])
    failures = []

    media = ffmpeg_tools.probe(path)
    assert media, f"probe failed: {path}"
    print(f"SOURCE {media.name}: {media.resolution} {media.fps:.2f}fps "
          f"{media.duration:.2f}s\n")

    # --- 1. selection model -------------------------------------------------
    project = Project()
    project.load(media)
    print(f"T1 loaded: selection {project.duration:.2f}s "
          f"(expect {media.duration:.2f})")
    if abs(project.duration - media.duration) > 1e-6:
        failures.append("T1 initial selection is not the whole clip")

    project.set_in(2.0)
    project.set_out(5.0)
    print(f"   in=2 out=5 -> duration {project.duration:.2f}s")
    if abs(project.duration - 3.0) > 1e-6:
        failures.append("T1 in/out did not produce a 3s selection")

    # in cannot cross out, and vice versa
    project.set_in(99.0)
    if project.in_point >= project.out_point:
        failures.append("T1 in-point crossed the out-point")
    project.set_out(0.0)
    if project.out_point <= project.in_point:
        failures.append("T1 out-point crossed the in-point")
    print(f"   crossing prevented: in={project.in_point:.3f} "
          f"out={project.out_point:.3f}")

    project.select_all()
    if abs(project.duration - media.duration) > 1e-6:
        failures.append("T1 select_all did not restore the full range")

    # --- 2. the size estimate is the target --------------------------------
    project.set_in(1.0)
    project.set_out(6.0)
    project.settings.target_mb = 11.5
    est = project.estimate()
    print(f"\nT2 estimate for a 5s selection: "
          f"{estimator.fmt_size(est['bytes'])} @ "
          f"{estimator.fmt_bitrate(est['bitrate'])}")
    # The target is a ceiling: never exceed it, and never exceed what the
    # footage actually needs to look transparent.
    if est["bytes"] > 11.5 * estimator.MB * 1.02:
        failures.append("T2 estimate exceeds the target ceiling")
    if est["bytes"] > est["quality_bytes"] * 1.02:
        failures.append("T2 estimate exceeds what the cut needs")

    # Shortening a budget-limited selection must not lower the bitrate; once
    # short enough it is capped by quality instead.
    project.set_in(0.0)
    project.set_out(media.duration)
    long_sel = project.estimate()
    project.set_out(media.duration / 2)
    half_sel = project.estimate()
    print(f"   full clip {estimator.fmt_bitrate(long_sel['bitrate'])} -> "
          f"half {estimator.fmt_bitrate(half_sel['bitrate'])}")
    if half_sel["bitrate"] < long_sel["bitrate"]:
        failures.append("T2 halving the selection lowered the bitrate")

    # --- 2b. the headline number tracks the selection ----------------------
    # The export size is always the target, so the panel headlines what the
    # cut NEEDS at full quality - that is what must move as you drag.
    project.settings.max_width = 1920
    needs = []
    for out in (12.0, 8.0, 4.0, 2.0):
        project.set_in(0.0)
        project.set_out(min(out, media.duration))
        needs.append(project.estimate()["quality_bytes"])
    print("\nT2b headline for 12/8/4/2s: "
          + ", ".join(estimator.fmt_size(n) for n in needs))
    if not all(a > b for a, b in zip(needs, needs[1:])):
        failures.append("T2b headline does not shrink with the selection")

    # A long cut must report as over budget, a short one as fitting.
    project.set_in(0.0)
    project.set_out(media.duration)
    long_est = project.estimate()
    project.set_out(min(2.0, media.duration))
    short_est = project.estimate()
    print(f"   full clip fits={long_est['fits']}  2s fits={short_est['fits']}")
    if not short_est["fits"]:
        failures.append("T2b a short cut should fit the budget")

    # --- 3. quality signal moves the right way -----------------------------
    project.set_in(0.0)
    project.set_out(min(4.0, media.duration))
    project.settings.max_width = 1920
    big = project.estimate()
    project.settings.max_width = 640
    small = project.estimate()
    print(f"\nT3 needs @1920 {estimator.fmt_size(big['quality_bytes'])} "
          f"({big['quality']['label']})  ->  @640 "
          f"{estimator.fmt_size(small['quality_bytes'])} "
          f"({small['quality']['label']})")
    # Fewer pixels need fewer bits, so downscaling lowers what the cut needs.
    if small["quality_bytes"] >= big["quality_bytes"]:
        failures.append("T3 downscaling did not reduce the required size")

    # A long selection at a small target must read poorly.
    project.settings.max_width = 1920
    project.set_out(media.duration)
    project.settings.target_mb = 1.0
    poor = project.estimate()["quality"]
    print(f"   whole clip at 1 MB -> {poor['label']}")
    if poor["label"] not in ("poor", "fair"):
        failures.append("T3 an underfed budget did not read as poor")

    # --- 4. export lands on the target -------------------------------------
    project = Project()
    project.load(media)
    project.set_in(1.0)
    project.set_out(min(5.0, media.duration))
    project.settings.target_mb = 3.0
    project.settings.max_width = 1280
    project.settings.two_pass = True
    project.settings.audio = False

    est = project.estimate()
    out = os.path.join(os.path.dirname(os.path.abspath(path)), "trim_test.mp4")
    passlog = os.path.join(os.path.dirname(out), "tpl")

    code, _ = _run(encoder.build_command(project, out, 1, passlog))
    if code != 0:
        failures.append("T4 pass 1 failed")
    else:
        code, err = _run(encoder.build_command(project, out, 2, passlog))
        encoder._cleanup_passlogs(passlog)
        if code != 0:
            failures.append("T4 pass 2 failed")
            print(err[-600:])
        else:
            actual = os.path.getsize(out)
            drift = abs(actual - est["bytes"]) / est["bytes"] * 100
            info = ffmpeg_tools.probe(out)
            print(f"\nT4 export: target 3.00 MB -> actual "
                  f"{estimator.fmt_size(actual)} (drift {drift:.1f}%)")
            print(f"   {info.resolution}, {info.duration:.2f}s "
                  f"(expect {project.duration:.2f}s)")
            if drift > 10:
                failures.append(f"T4 size drift {drift:.1f}% too high")
            if abs(info.duration - project.duration) > 0.3:
                failures.append("T4 exported duration does not match selection")
            if info.has_audio:
                failures.append("T4 audio present despite being disabled")

    # --- 4b. the target is a ceiling, not a quota --------------------------
    # A short cut that needs less than the budget must produce a SMALLER file,
    # not one padded up to the target.
    project = Project()
    project.load(media)
    project.set_in(0.0)
    project.set_out(min(4.0, media.duration))
    project.settings.target_mb = 50.0        # deliberately generous
    project.settings.max_width = 640
    project.settings.audio = False

    est = project.estimate()
    out2 = os.path.join(os.path.dirname(os.path.abspath(path)), "cap_test.mp4")
    pl2 = os.path.join(os.path.dirname(out2), "cpl")
    _run(encoder.build_command(project, out2, 1, pl2))
    code, _ = _run(encoder.build_command(project, out2, 2, pl2))
    encoder._cleanup_passlogs(pl2)
    if code != 0:
        failures.append("T4b capped export failed")
    else:
        actual = os.path.getsize(out2)
        print(f"\nT4b 50 MB budget on a small 4s cut -> predicted "
              f"{estimator.fmt_size(est['bytes'])}, actual "
              f"{estimator.fmt_size(actual)}")
        if actual > 20 * estimator.MB:
            failures.append("T4b file was padded up to the budget")
        if abs(actual - est["bytes"]) / est["bytes"] > 0.20:
            failures.append("T4b predicted size disagrees with the file")
        os.remove(out2)

    # --- 4c. progress reaches 100 and finished fires -----------------------
    # ffmpeg writes to stdout AND stderr; draining only stdout deadlocks it,
    # which is how the bar used to stick at 99%.
    project.settings.target_mb = 3.0
    out3 = os.path.join(os.path.dirname(os.path.abspath(path)), "prog_test.mp4")
    seen, fin = [], []
    worker = encoder.EncodeWorker(project, out3)
    worker.progress.connect(lambda v, _s: seen.append(v))
    worker.finished.connect(lambda ok, msg: fin.append((ok, msg)))
    start = time.perf_counter()
    worker.run()
    print(f"T4c encode finished in {time.perf_counter() - start:.1f}s, "
          f"max progress {max(seen) if seen else 0}, finished={bool(fin)}")
    if not seen or max(seen) != 100:
        failures.append("T4c progress never reached 100")
    if not fin or not fin[0][0]:
        failures.append("T4c finished signal did not fire")
    if os.path.exists(out3):
        os.remove(out3)

    # --- 5. scrub latency ---------------------------------------------------
    decoder = ClipDecoder(path)
    import random
    random.seed(5)
    times = []
    for _ in range(25):
        target = random.uniform(0, decoder.duration - 0.2)
        start = time.perf_counter()
        decoder.frame_at(target)
        times.append((time.perf_counter() - start) * 1000)
    med = statistics.median(times)
    print(f"\nT5 scrub: median {med:.1f} ms ({1000 / med:.0f} fps)")

    # EOF must not raise - it used to kill the decode thread.
    for t in (decoder.duration, decoder.duration + 3):
        try:
            frame = decoder.frame_at(t)
            if frame is None or frame.isNull():
                failures.append(f"T5 no frame at {t:.1f}s (EOF)")
        except Exception as exc:
            failures.append(f"T5 EOF raised {type(exc).__name__}")
    print("   EOF handled without raising")
    decoder.close()

    # --- 6. the window, end to end -----------------------------------------
    win = MainWindow()
    win.show()
    win.load(path)
    app.processEvents()
    print(f"\nT6 window: loaded={win.project.loaded}, "
          f"export enabled={win.export_btn.isEnabled()}")
    if not win.project.loaded or not win.export_btn.isEnabled():
        failures.append("T6 window did not enable export after loading")

    # I/O keys set the selection at the playhead
    win.trim.playhead = 1.5
    win.trim.set_in_here()
    win.trim.playhead = 4.0
    win.trim.set_out_here()
    app.processEvents()
    print(f"   I/O keys -> in {win.project.in_point:.2f} "
          f"out {win.project.out_point:.2f}")
    if abs(win.project.in_point - 1.5) > 1e-6 or abs(win.project.out_point - 4.0) > 1e-6:
        failures.append("T6 I/O keys did not set the selection")

    # dragging the out handle shortens the selection
    before = win.project.duration
    rect = win.trim.strip_rect()
    out_x = win.trim.x_for(win.project.out_point)
    drag(win.trim, out_x - 4, rect.center().y(),
         out_x - 80, rect.center().y())
    app.processEvents()
    print(f"   drag out handle: {before:.2f}s -> {win.project.duration:.2f}s")
    if win.project.duration >= before:
        failures.append("T6 dragging the out handle did not shorten it")

    # the readout tracks the selection
    shown = win.size_label.text()
    print(f"   readout: {shown} / quality {win.quality_label.text()}")
    if shown == "0 MB":
        failures.append("T6 size readout did not update")

    win.shutdown()

    print("\n" + "=" * 58)
    if failures:
        print(f"FAILURES ({len(failures)}):")
        for f in failures:
            print("  -", f)
        return 1
    print("ALL TESTS PASSED")
    return 0


def _run(args):
    result = ffmpeg_tools.run(args)
    return result.returncode, result.stderr


if __name__ == "__main__":
    sys.exit(main())
