import logging
import threading

MAX_THREADS = 100  # defensive ceiling on Windows


def _warn_thread_limit(active: int, context: str, count: int) -> None:
    logging.warning(
        "[ThreadGuard] Refusing to spawn new thread(s) "
        f"(active={active}, limit={MAX_THREADS}, requested={count}, context={context})"
    )


def can_spawn_threads(count: int, context: str = "unknown") -> bool:
    active = threading.active_count()
    if active + max(1, count) > MAX_THREADS:
        _warn_thread_limit(active, context, count)
        return False
    return True


def can_spawn_thread(context: str = "unknown") -> bool:
    return can_spawn_threads(1, context=context)
