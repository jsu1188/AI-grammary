import threading
import time

try:
    from pynput import keyboard
except ImportError:  # pragma: no cover - optional dependency fallback
    keyboard = None


def monitor_typed_text(
    callback,
    idle_timeout=0.45,
    reset_timeout=6.0,
    poll_interval=0.12,
    max_length=4000,
):
    if keyboard is None:
        return

    state = {
        "buffer": "",
        "dirty": False,
        "last_key_time": 0.0,
        "last_emit_text": "",
    }
    lock = threading.Lock()

    def on_press(key):
        now = time.time()
        with lock:
            if state["last_key_time"] and now - state["last_key_time"] > reset_timeout:
                state["buffer"] = ""

            appended = False

            if hasattr(key, "char") and key.char:
                state["buffer"] += key.char
                appended = True
            elif key == keyboard.Key.space:
                state["buffer"] += " "
                appended = True
            elif key == keyboard.Key.enter:
                state["buffer"] += "\n"
                appended = True
            elif key == keyboard.Key.tab:
                state["buffer"] += "\t"
                appended = True
            elif key == keyboard.Key.backspace and state["buffer"]:
                state["buffer"] = state["buffer"][:-1]
                appended = True

            if appended:
                state["buffer"] = state["buffer"][-max_length:]
                state["dirty"] = True
                state["last_key_time"] = now

    listener = keyboard.Listener(on_press=on_press)
    listener.daemon = True
    listener.start()

    while True:
        time.sleep(poll_interval)
        with lock:
            if not state["dirty"] or not state["buffer"].strip():
                continue
            if time.time() - state["last_key_time"] < idle_timeout:
                continue

            text = state["buffer"]
            if text == state["last_emit_text"]:
                state["dirty"] = False
                continue

            state["dirty"] = False
            state["last_emit_text"] = text

        callback(text)
