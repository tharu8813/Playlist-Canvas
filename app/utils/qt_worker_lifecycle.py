"""The one verified way to stop and delete a cancelled QThread on shutdown.

Every controller that owns a worker thread must end it through
``stop_qthread_now``. The order below is what fixed real Windows 0xC0000409
fail-fast crashes; keeping a single copy means a fix here reaches all of them.
"""

from __future__ import annotations

from PySide6.QtCore import SIGNAL, QCoreApplication, QEvent, QEventLoop, QThread, QTimer


def stop_qthread_now(worker: QThread | None) -> None:
    """Wait out an already-cancelled worker, then delete it right now.

    1. Disconnect ``finished`` so the owner's own "forget and deleteLater"
       handler can't also schedule deletion while this waits on the object.
    2. Wait in a nested loop that excludes user input: paint and queued
       completion signals keep flowing, but no click can re-enter the owner's
       destruction path.
    3. ``wait()``, ``deleteLater()`` and force that DeferredDelete now. A plain
       deleteLater() ran inside some *other* controller's nested shutdown loop
       (MainWindow shuts several down in sequence), interleaved with a still
       live thread's completion -- which reproduced the crash deterministically.
    """
    if worker is None:
        return
    try:
        running = worker.isRunning()
    except RuntimeError:
        return  # the C++ QThread is already gone
    if worker.receivers(SIGNAL("finished()")) > 0:
        worker.finished.disconnect()
    if running:
        loop = QEventLoop()
        worker.finished.connect(loop.quit)
        # QThread emits finished *before* it stops reporting isRunning(), so a
        # finish landing between the check above and connect() would never
        # quit the loop. Polling isFinished() closes that window.
        poll = QTimer()
        poll.timeout.connect(lambda: loop.quit() if worker.isFinished() else None)
        poll.start(50)
        if not worker.isFinished():
            loop.exec(QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)
        poll.stop()
    try:
        worker.wait()
    except RuntimeError:
        return
    worker.deleteLater()
    QCoreApplication.sendPostedEvents(worker, QEvent.Type.DeferredDelete)
