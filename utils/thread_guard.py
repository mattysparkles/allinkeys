import threading
import logging

MAX_THREADS = 200  # safe ceiling on Windows


def can_spawn_thread(context: str = "unknown") -> bool:
    active = threading.active_count()
    if active >= MAX_THREADS:
        logging.warning(
            f"[ThreadGuard] Refusing to spawn new thread "
            f"(active={active}, limit={MAX_THREADS}, context={context})"
        )
        return False
    return True
