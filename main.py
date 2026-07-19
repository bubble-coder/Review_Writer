"""Application entry point."""

from datetime import datetime
import sys
import traceback

from review_writer.app_paths import resource_path, user_data_root
from review_writer.ui import launch


def _write_crash_log(error_type: type[BaseException], error: BaseException, error_traceback: object) -> None:
    """Persist startup failures for windowed builds that have no console."""

    try:
        log_directory = user_data_root() / "logs"
        log_directory.mkdir(parents=True, exist_ok=True)
        with (log_directory / "crash.log").open("a", encoding="utf-8") as handle:
            handle.write(f"\n[{datetime.now().astimezone().isoformat(timespec='seconds')}]\n")
            traceback.print_exception(error_type, error, error_traceback, file=handle)
    except OSError:
        pass


def _smoke_test() -> None:
    """Exercise frozen resources and construct the main window, then exit."""

    import tkinter as tk

    import pdfplumber  # noqa: F401
    import pypdf  # noqa: F401

    from review_writer.model_catalog import load_model_catalog_document
    from review_writer.ui import ResearchPlannerApp

    load_model_catalog_document()
    downloader = resource_path("vendor", "nature-downloader", "scripts", "batch_download.mjs")
    if not downloader.is_file():
        raise FileNotFoundError(f"Missing bundled downloader: {downloader}")
    root = tk.Tk()
    root.withdraw()
    app = ResearchPlannerApp(root)
    root.update_idletasks()
    app.destroy()
    root.destroy()


def main() -> None:
    """Launch the desktop research-planning application."""

    sys.excepthook = _write_crash_log
    if "--smoke-test" in sys.argv[1:]:
        _smoke_test()
        return
    launch()


if __name__ == "__main__":
    main()
