from __future__ import annotations


def patch_multiprocess_resource_tracker() -> None:
    """Work around a known multiprocess/Python 3.12 Windows shutdown bug."""
    try:
        from multiprocess import resource_tracker  # type: ignore
    except Exception:
        return
    original = getattr(resource_tracker.ResourceTracker, "_stop_locked", None)
    if original is None:
        return
    if getattr(original, "__name__", "") == "_safe_stop_locked":
        return

    def _safe_stop_locked(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        try:
            return original(self, *args, **kwargs)
        except AttributeError as exc:
            if "_recursion_count" in str(exc):
                return None
            raise

    resource_tracker.ResourceTracker._stop_locked = _safe_stop_locked  # type: ignore[attr-defined]

