from __future__ import annotations

import re


def preserve_blank_lines(original: str, replacement: str) -> str:
    """Reinsert blank-line positions from original when content line counts match."""
    original_text = str(original or "")
    replacement_text = str(replacement or "")
    if not original_text or not replacement_text:
        return replacement_text

    original_newline = _dominant_newline(original_text)
    original_lines = _normalize_newlines(original_text).split("\n")
    replacement_lines = _normalize_newlines(replacement_text).split("\n")

    if not any(_is_blank(line) for line in original_lines):
        return replacement_text

    original_content_count = sum(1 for line in original_lines if not _is_blank(line))
    replacement_content_lines = [line for line in replacement_lines if not _is_blank(line)]
    if original_content_count != len(replacement_content_lines):
        return replacement_text

    content_index = 0
    restored_lines: list[str] = []
    for original_line in original_lines:
        if _is_blank(original_line):
            restored_lines.append("")
            continue
        restored_lines.append(replacement_content_lines[content_index])
        content_index += 1

    return _join_like_original(original_text, original_newline, restored_lines)


def preserve_replacement_structure(original: str, replacement: str) -> str:
    """Preserve the original visible line layout as closely as possible."""
    original_text = str(original or "")
    replacement_text = str(replacement or "")
    if not original_text or not replacement_text:
        return replacement_text

    restored = preserve_blank_lines(original_text, replacement_text)
    if restored != replacement_text:
        replacement_text = restored

    paragraph_restored = _restore_paragraph_line_layout(original_text, replacement_text)
    if paragraph_restored != replacement_text:
        return paragraph_restored

    content_restored = _restore_content_line_layout(original_text, replacement_text)
    if content_restored != replacement_text:
        return content_restored

    return _restore_collapsed_marked_lines(original_text, replacement_text)


def _restore_paragraph_line_layout(original: str, replacement: str) -> str:
    original_text = str(original or "")
    replacement_text = str(replacement or "")
    if not original_text or not replacement_text:
        return replacement_text

    original_newline = _dominant_newline(original_text)
    original_lines = _normalize_newlines(original_text).split("\n")
    replacement_lines = _normalize_newlines(replacement_text).split("\n")
    original_paragraphs = _extract_paragraphs(original_lines)
    replacement_paragraphs = _extract_paragraphs(replacement_lines)

    if not original_paragraphs or len(original_paragraphs) != len(replacement_paragraphs):
        return replacement_text

    restored_lines = list(original_lines)
    changed = False
    for original_paragraph, replacement_paragraph in zip(original_paragraphs, replacement_paragraphs):
        original_content = [original_lines[index] for index in original_paragraph]
        replacement_content = [replacement_lines[index] for index in replacement_paragraph]
        reflowed = _reflow_to_original_lines(original_content, replacement_content)
        if len(reflowed) != len(original_paragraph):
            return replacement_text
        for index, content in zip(original_paragraph, reflowed):
            if restored_lines[index] != content:
                restored_lines[index] = content
                changed = True

    if not changed:
        return replacement_text
    return _join_like_original(original_text, original_newline, restored_lines)


def _restore_content_line_layout(original: str, replacement: str) -> str:
    original_text = str(original or "")
    replacement_text = str(replacement or "")
    if not original_text or not replacement_text:
        return replacement_text

    original_newline = _dominant_newline(original_text)
    original_lines = _normalize_newlines(original_text).split("\n")
    replacement_lines = _normalize_newlines(replacement_text).split("\n")

    original_content_lines = [line for line in original_lines if not _is_blank(line)]
    replacement_content_lines = [line for line in replacement_lines if not _is_blank(line)]
    if not original_content_lines or not replacement_content_lines:
        return replacement_text

    reflowed = _reflow_to_original_lines(original_content_lines, replacement_content_lines)
    if len(reflowed) != len(original_content_lines):
        return replacement_text

    content_index = 0
    restored_lines: list[str] = []
    changed = False
    for original_line in original_lines:
        if _is_blank(original_line):
            restored_lines.append("")
            continue
        new_line = reflowed[content_index]
        restored_lines.append(new_line)
        if new_line != replacement_content_lines[min(content_index, len(replacement_content_lines) - 1)]:
            changed = True
        content_index += 1

    if not changed:
        return replacement_text
    return _join_like_original(original_text, original_newline, restored_lines)


def _reflow_to_original_lines(original_lines: list[str], replacement_lines: list[str]) -> list[str]:
    target_count = len(original_lines)
    if target_count == 0:
        return []

    flattened_replacement = _flatten_lines(replacement_lines)
    if not flattened_replacement:
        return ["" for _ in range(target_count)]

    reflowed: list[str] = []
    remaining = flattened_replacement
    target_lengths = [_target_width(line) for line in original_lines]

    for index, target_length in enumerate(target_lengths):
        remaining_slots = target_count - index
        if remaining_slots <= 1:
            reflowed.append(remaining.strip())
            remaining = ""
            continue
        if not remaining:
            reflowed.append("")
            continue

        max_take = max(1, len(remaining) - (remaining_slots - 1))
        desired = max(1, min(target_length, max_take))
        split_at = _find_split_index(remaining, desired, max_take)
        segment = remaining[:split_at].rstrip()
        if not segment and remaining:
            split_at = min(max_take, max(1, desired))
            segment = remaining[:split_at].rstrip()
        reflowed.append(segment)
        remaining = remaining[split_at:].lstrip()

    if remaining:
        reflowed[-1] = (reflowed[-1].rstrip() + " " + remaining).strip()
    return reflowed


def _find_split_index(text: str, desired: int, max_take: int) -> int:
    text_length = len(text)
    if text_length <= desired:
        return text_length

    lower_bound = max(1, min(desired, max_take))
    upper_bound = min(text_length, max_take)
    candidate = _best_boundary_before(text, lower_bound)
    if candidate:
        return candidate

    candidate = _best_boundary_after(text, desired, upper_bound)
    if candidate:
        return candidate

    punctuation_candidate = _best_punctuation_before(text, lower_bound)
    if punctuation_candidate:
        return punctuation_candidate

    return lower_bound


def _best_boundary_before(text: str, limit: int) -> int:
    matches = list(re.finditer(r"\s+", text[:limit + 1]))
    if not matches:
        return 0
    return matches[-1].end()


def _best_boundary_after(text: str, start: int, limit: int) -> int:
    if limit <= start:
        return 0
    segment = text[start:limit]
    match = re.search(r"\s+", segment)
    if not match:
        return 0
    return start + match.end()


def _best_punctuation_before(text: str, limit: int) -> int:
    punctuation_marks = ",.:;!?)]}"
    for index in range(limit - 1, -1, -1):
        if text[index] in punctuation_marks:
            return index + 1
    return 0


def _flatten_lines(lines: list[str]) -> str:
    parts = [line.strip() for line in lines if not _is_blank(line)]
    return " ".join(part for part in parts if part).strip()


def _target_width(line: str) -> int:
    stripped = line.strip()
    if not stripped:
        return 1
    return max(1, len(stripped))


def _extract_paragraphs(lines: list[str]) -> list[list[int]]:
    paragraphs: list[list[int]] = []
    current: list[int] = []
    for index, line in enumerate(lines):
        if _is_blank(line):
            if current:
                paragraphs.append(current)
                current = []
            continue
        current.append(index)
    if current:
        paragraphs.append(current)
    return paragraphs


def _restore_collapsed_marked_lines(original: str, replacement: str) -> str:
    original_text = str(original or "")
    replacement_text = str(replacement or "")
    if not original_text or not replacement_text:
        return replacement_text

    original_newline = _dominant_newline(original_text)
    original_lines = _normalize_newlines(original_text).split("\n")
    replacement_lines = _normalize_newlines(replacement_text).split("\n")
    original_content_lines = [line for line in original_lines if not _is_blank(line)]
    replacement_content_lines = [line for line in replacement_lines if not _is_blank(line)]

    if len(original_content_lines) <= 1 or len(replacement_content_lines) != 1:
        return replacement_text

    replacement_line = replacement_content_lines[0]
    suffix = _temporary_marker_suffix(replacement_line, original_content_lines)
    if not suffix:
        return replacement_text

    restored_lines = list(original_lines)
    for index in range(len(restored_lines) - 1, -1, -1):
        if not _is_blank(restored_lines[index]):
            restored_lines[index] = restored_lines[index].rstrip() + suffix
            break

    return _join_like_original(original_text, original_newline, restored_lines)


def _temporary_marker_suffix(replacement_line: str, original_content_lines: list[str]) -> str:
    marker_start = replacement_line.rfind(" [")
    if marker_start < 0 or not replacement_line.rstrip().endswith("]"):
        return ""

    marker_suffix = replacement_line[marker_start:]
    for original_line in original_content_lines:
        content = original_line.rstrip()
        if content and replacement_line.startswith(content):
            suffix = replacement_line[len(content):]
            if suffix.startswith(" [") and suffix.rstrip().endswith("]"):
                return suffix
    return marker_suffix


def _join_like_original(original_text: str, newline: str, lines: list[str]) -> str:
    joined = newline.join(lines)
    if original_text.endswith(("\r\n", "\n", "\r")) and not joined.endswith(newline):
        joined += newline
    return joined


def _normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _dominant_newline(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


def _is_blank(line: str) -> bool:
    return not line.strip()
