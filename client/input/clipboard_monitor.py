import time

import pyperclip


def monitor_clipboard(callback, initial_text="", poll_interval=0.25):
    last_text = initial_text
    first_sample = True
    last_time = 0.0

    while True:
        text = _safe_paste()
        if text is None:
            time.sleep(poll_interval)
            continue

        if first_sample:
            first_sample = False
            if text != last_text and text.strip():
                last_text = text
                last_time = time.time()
                callback(text)
            time.sleep(poll_interval)
            continue

        if text != last_text and text.strip():
            current_time = time.time()
            if current_time - last_time > 0.6:
                last_text = text
                last_time = current_time
                callback(text)

        time.sleep(poll_interval)


def _safe_paste(retries=3, retry_delay=0.05):
    for _ in range(retries):
        try:
            return pyperclip.paste()
        except pyperclip.PyperclipException:
            time.sleep(retry_delay)
        except OSError:
            time.sleep(retry_delay)
    return None
