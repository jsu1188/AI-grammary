(() => {
  const BRIDGE = "http://127.0.0.1:8766";
  const SESSION_ID = `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  const POLL_MS = 500;
  const CAPTURE_DEBOUNCE_MS = 600;

  let captureTimer = null;
  let settleTimers = [];
  let lastSignature = "";
  let lastCaptureSentAt = 0;
  let lastEditable = null;
  let observedEditable = null;
  let editableObserver = null;

  console.info("[Writing Assistant Bridge] content script loaded", {
    sessionId: SESSION_ID,
    url: location.href
  });

  function isEditableElement(node) {
    if (!node || node.nodeType !== Node.ELEMENT_NODE) return false;
    const tag = node.tagName.toLowerCase();
    return tag === "textarea" || tag === "input" || node.isContentEditable;
  }

  function activeEditable() {
    const active = document.activeElement;
    if (isEditableElement(active)) {
      lastEditable = active;
      observeEditable(active);
      return active;
    }
    const selection = window.getSelection();
    if (!selection || selection.rangeCount === 0) return fallbackEditable();
    let node = selection.anchorNode;
    if (node && node.nodeType === Node.TEXT_NODE) node = node.parentElement;
    const editable = node && node.closest ? node.closest("[contenteditable=''],[contenteditable='true'],textarea,input") : null;
    if (editable) {
      lastEditable = editable;
      observeEditable(editable);
    }
    if (editable) return editable;
    return fallbackEditable();
  }

  function fallbackEditable() {
    if (isUsableEditable(lastEditable)) return lastEditable;
    const fallback = bestEditableCandidate();
    if (fallback) {
      lastEditable = fallback;
      observeEditable(fallback);
      return fallback;
    }
    return null;
  }

  function isUsableEditable(element) {
    return Boolean(element && document.contains(element) && isEditableElement(element));
  }

  function bestEditableCandidate() {
    const candidates = Array.from(document.querySelectorAll("textarea,input,[contenteditable=''],[contenteditable='true']"))
      .filter((element) => isVisibleEditable(element) && editableCandidateText(element).trim());
    if (!candidates.length) return null;
    if (candidates.length === 1) return candidates[0];
    return candidates.sort((left, right) => {
      const leftScore = editableCandidateText(left).length + visibleArea(left) / 100;
      const rightScore = editableCandidateText(right).length + visibleArea(right) / 100;
      return rightScore - leftScore;
    })[0];
  }

  function isVisibleEditable(element) {
    const rect = element.getBoundingClientRect();
    const style = window.getComputedStyle(element);
    return rect.width > 0 && rect.height > 0 && style.visibility !== "hidden" && style.display !== "none";
  }

  function editableCandidateText(element) {
    if (!element) return "";
    if (element.matches("textarea,input")) return element.value || "";
    return element.innerText || element.textContent || "";
  }

  function visibleArea(element) {
    const rect = element.getBoundingClientRect();
    return Math.max(0, rect.width) * Math.max(0, rect.height);
  }

  function captureInput(element) {
    const value = element.value || "";
    const start = element.selectionStart || 0;
    const end = element.selectionEnd || start;
    const hasSelection = end > start;
    const text = hasSelection ? value.slice(start, end) : value;
    return {
      text,
      html: "",
      target_kind: element.tagName.toLowerCase(),
      selection: {
        start,
        end,
        selected: hasSelection
      },
      segments: [{ start: 0, end: text.length, style: styleFromElement(element) }]
    };
  }

  function captureContentEditable(element) {
    const range = selectedRangeInside(element);
    const elementText = editablePlainText(element);
    const hasSelection = !!range;
    const text = hasSelection ? String(range.toString() || "") : elementText;
    const segments = hasSelection ? styleSegmentsFromRange(range, element, text) : styleSegmentsFromElement(element, text);
    return {
      text,
      html: hasSelection ? fragmentHtml(range) : element.innerHTML,
      target_kind: "contenteditable",
      dom_debug: domDebug(element, range),
      selection: { selected: hasSelection },
      segments
    };
  }

  function normalizeComparableText(text) {
    return String(text || "").replace(/\r\n/g, "\n").replace(/\r/g, "\n").trim();
  }

  function editablePlainText(element) {
    const blockSelector = "p,div,li,h1,h2,h3,h4,h5,h6,blockquote,pre";
    const blocks = leafTextBlocks(element, blockSelector);
    if (!blocks.length) {
      return inlinePlainText(element);
    }

    const lines = blocks.map((block) => blockPlainText(block));
    return lines.join("\n");
  }

  function leafTextBlocks(element, blockSelector) {
    const allBlocks = Array.from(element.querySelectorAll(blockSelector));
    return allBlocks.filter((block) => {
      const nestedBlocks = Array.from(block.querySelectorAll(blockSelector));
      return !nestedBlocks.some((nested) => nested !== block);
    });
  }

  function blockPlainText(block) {
    if (isEmptyBlock(block)) return "";
    const raw = block.innerText != null && block.innerText !== "" ? block.innerText : block.textContent || "";
    const value = String(raw).replace(/\r\n/g, "\n").replace(/\r/g, "\n");
    return value.replace(/\n+$/g, "");
  }

  function isEmptyBlock(block) {
    return !String(block.textContent || "").trim() && Boolean(block.querySelector("br") || block.children.length === 0);
  }

  function inlinePlainText(element) {
    const chunks = [];
    walkInlineText(element, chunks);
    return chunks.join("").replace(/\r\n/g, "\n").replace(/\r/g, "\n").replace(/\n+$/g, "");
  }

  function walkInlineText(node, chunks) {
    if (node.nodeType === Node.TEXT_NODE) {
      chunks.push(node.nodeValue || "");
      return;
    }
    if (node.nodeType !== Node.ELEMENT_NODE) return;
    if (node.tagName && node.tagName.toLowerCase() === "br") {
      chunks.push("\n");
      return;
    }
    Array.from(node.childNodes).forEach((child) => walkInlineText(child, chunks));
  }

  function domDebug(element, range) {
    const textNodes = [];
    const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT, {
      acceptNode(node) {
        return (node.nodeValue || "").trim() ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_SKIP;
      }
    });
    while (walker.nextNode() && textNodes.length < 8) {
      const node = walker.currentNode;
      const parent = node.parentElement;
      textNodes.push({
        text: (node.nodeValue || "").slice(0, 40),
        parent: parent ? parent.tagName.toLowerCase() : "",
        parentClass: parent ? parent.className || "" : "",
        parentStyle: parent ? parent.getAttribute("style") || "" : "",
        style: parent ? styleFromElement(parent) : {}
      });
    }
    return {
      textPreview: editablePlainText(element).slice(0, 500),
      htmlPreview: (element.innerHTML || "").slice(0, 500),
      childElementCount: element.querySelectorAll("*").length,
      hasRange: Boolean(range),
      textNodes
    };
  }

  function selectedRangeInside(element) {
    const selection = window.getSelection();
    if (!selection || selection.rangeCount === 0 || selection.isCollapsed) return null;
    const range = selection.getRangeAt(0);
    const container = range.commonAncestorContainer.nodeType === Node.ELEMENT_NODE
      ? range.commonAncestorContainer
      : range.commonAncestorContainer.parentElement;
    return container && element.contains(container) ? range : null;
  }

  function fragmentHtml(range) {
    const container = document.createElement("div");
    container.appendChild(range.cloneContents());
    return container.innerHTML;
  }

  function styleSegmentsFromRange(range, fallbackElement, text) {
    if (!text) return [];
    if (!range) {
      return [{ start: 0, end: text.length, style: styleFromElement(fallbackElement) }];
    }

    const walkerRoot = range.commonAncestorContainer.nodeType === Node.ELEMENT_NODE
      ? range.commonAncestorContainer
      : range.commonAncestorContainer.parentElement;
    if (!walkerRoot) return [{ start: 0, end: text.length, style: styleFromElement(fallbackElement) }];

    const walker = document.createTreeWalker(walkerRoot, NodeFilter.SHOW_TEXT, {
      acceptNode(node) {
        return range.intersectsNode(node) ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT;
      }
    });

    const segments = [];
    let position = 0;
    while (walker.nextNode()) {
      const node = walker.currentNode;
      let chunk = node.nodeValue || "";
      if (!chunk) continue;
      if (node === range.startContainer) chunk = chunk.slice(range.startOffset);
      if (node === range.endContainer) chunk = chunk.slice(0, Math.max(0, range.endOffset - (node === range.startContainer ? range.startOffset : 0)));
      if (!chunk) continue;
      const style = styleFromElement(node.parentElement || fallbackElement);
      appendSegment(segments, position, position + chunk.length, style);
      position += chunk.length;
    }

    return segments.length ? segments : [{ start: 0, end: text.length, style: styleFromElement(fallbackElement) }];
  }

  function styleSegmentsFromElement(element, text) {
    if (!text) return [];
    const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT, {
      acceptNode(node) {
        return (node.nodeValue || "").length ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_SKIP;
      }
    });

    const segments = [];
    let position = 0;
    while (walker.nextNode()) {
      const node = walker.currentNode;
      const chunk = node.nodeValue || "";
      if (!chunk) continue;
      const foundAt = text.indexOf(chunk, position);
      const start = foundAt >= 0 ? foundAt : position;
      const end = Math.min(text.length, start + chunk.length);
      if (end <= start) continue;
      appendSegment(segments, start, end, styleFromElement(node.parentElement || element));
      position = end;
    }

    if (!segments.length) {
      return [{ start: 0, end: text.length, style: styleFromElement(element) }];
    }
    if (segments[0].start > 0) {
      segments.unshift({ start: 0, end: segments[0].start, style: styleFromElement(element) });
    }
    const last = segments[segments.length - 1];
    if (last.end < text.length) {
      appendSegment(segments, last.end, text.length, styleFromElement(element));
    }
    return segments;
  }

  function styleFromElement(element) {
    const computed = window.getComputedStyle(element);
    return {
      fontFamily: computed.fontFamily,
      fontSize: computed.fontSize,
      fontWeight: computed.fontWeight,
      fontStyle: computed.fontStyle,
      color: computed.color,
      textDecorationLine: computed.textDecorationLine,
      backgroundColor: computed.backgroundColor
    };
  }

  function appendSegment(segments, start, end, style) {
    const signature = JSON.stringify(style);
    const previous = segments[segments.length - 1];
    if (previous && previous.signature === signature && previous.end === start) {
      previous.end = end;
      return;
    }
    segments.push({ start, end, style, signature });
  }

  function cleanSegments(segments) {
    return segments.map(({ signature, ...segment }) => segment);
  }

  function scheduleCapture() {
    window.clearTimeout(captureTimer);
    captureTimer = window.setTimeout(captureNow, CAPTURE_DEBOUNCE_MS);
  }

  function scheduleSettledCaptures() {
    settleTimers.forEach((timer) => window.clearTimeout(timer));
    settleTimers = [0, 250, 800, 1500, 2500].map((delay) => window.setTimeout(captureNow, delay));
  }

  function observeEditable(element) {
    if (!element || element.matches("textarea,input") || observedEditable === element) return;
    if (editableObserver) editableObserver.disconnect();
    observedEditable = element;
    editableObserver = new MutationObserver(() => scheduleCapture());
    editableObserver.observe(element, {
      childList: true,
      characterData: true,
      subtree: true
    });
  }

  async function captureNow() {
    const element = activeEditable();
    if (!element) return;
    const payload = element.matches("textarea,input") ? captureInput(element) : captureContentEditable(element);
    if (!payload.text.trim()) return;
    payload.segments = cleanSegments(payload.segments || []);
    payload.session_id = SESSION_ID;
    payload.url = location.href;
    payload.title = document.title;

    const signature = JSON.stringify([payload.url, payload.text, payload.html, payload.selection, payload.segments]);
    const now = Date.now();
    if (signature === lastSignature && now - lastCaptureSentAt < 5000) return;

    try {
      const response = await fetch(`${BRIDGE}/capture`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
      if (response.ok) {
        lastSignature = signature;
        lastCaptureSentAt = now;
      }
    } catch (_error) {
      // The desktop app may not be running yet.
    }
  }

  async function pollCommand() {
    try {
      const response = await fetch(`${BRIDGE}/command?session_id=${encodeURIComponent(SESSION_ID)}`);
      const data = await response.json();
      if (data && data.command && data.command.type === "replace_selection") {
        applyReplacement(data.command.text || "", data.command.style_info || {});
        scheduleCapture();
      }
    } catch (_error) {
      // Keep polling quietly.
    } finally {
      window.setTimeout(pollCommand, POLL_MS);
    }
  }

  function applyReplacement(text, styleInfo) {
    const element = activeEditable();
    if (!element) return;
    if (element.matches("textarea,input")) {
      const savedSelection = styleInfo && styleInfo.selection ? styleInfo.selection : {};
      const start = savedSelection.selected ? savedSelection.start || 0 : element.selectionStart || 0;
      const end = savedSelection.selected ? savedSelection.end || start : element.selectionEnd || start;
      element.setRangeText(text, start, end, "end");
      element.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "insertReplacementText", data: text }));
      element.dispatchEvent(new Event("change", { bubbles: true }));
      return;
    }

    const selection = window.getSelection();
    const range = selectedRangeInside(element) || rangeForElementContents(element);
    const beforeDebug = domDebug(element, range);
    if (replaceTextPreservingDom(range, element, text)) {
      selection.removeAllRanges();
      const newRange = document.createRange();
      newRange.selectNodeContents(element);
      newRange.collapse(false);
      selection.addRange(newRange);
      element.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "insertReplacementText", data: text }));
      element.dispatchEvent(new Event("change", { bubbles: true }));
      reportApplied("dom_preserve", text, beforeDebug, domDebug(element, rangeForElementContents(element)));
      return;
    }

    range.deleteContents();
    const fragment = buildReplacementFragment(text, styleInfo, beforeDebug);
    range.insertNode(fragment);
    selection.removeAllRanges();
    const newRange = document.createRange();
    newRange.selectNodeContents(element);
    newRange.collapse(false);
    selection.addRange(newRange);
    element.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "insertReplacementText", data: text }));
    element.dispatchEvent(new Event("change", { bubbles: true }));
    reportApplied("fragment", text, beforeDebug, domDebug(element, rangeForElementContents(element)));
  }

  async function reportApplied(method, text, before, after) {
    try {
      await fetch(`${BRIDGE}/applied`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          session_id: SESSION_ID,
          method,
          text,
          before,
          after,
          url: location.href,
          title: document.title
        })
      });
    } catch (_error) {
      // Diagnostics are best effort.
    }
  }

  function replaceTextPreservingDom(range, element, text) {
    if (String(text).includes("\n")) return false;
    const entries = textNodeEntriesForRange(range, element);
    if (!entries.length) return false;

    let cursor = 0;
    entries.forEach((entry, index) => {
      const original = entry.node.nodeValue || "";
      const before = original.slice(0, entry.startOffset);
      const after = original.slice(entry.endOffset);
      const originalLength = Math.max(0, entry.endOffset - entry.startOffset);
      const isLast = index === entries.length - 1;
      const nextCursor = isLast ? text.length : Math.min(text.length, cursor + originalLength);
      const chunk = text.slice(cursor, nextCursor);
      entry.node.nodeValue = before + chunk + after;
      cursor = nextCursor;
    });
    return true;
  }

  function textNodeEntriesForRange(range, element) {
    const entries = [];
    const root = element;
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
      acceptNode(node) {
        return range.intersectsNode(node) ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT;
      }
    });

    while (walker.nextNode()) {
      const node = walker.currentNode;
      const value = node.nodeValue || "";
      let startOffset = 0;
      let endOffset = value.length;
      if (node === range.startContainer) startOffset = range.startOffset;
      if (node === range.endContainer) endOffset = range.endOffset;
      if (endOffset > startOffset) {
        entries.push({ node, startOffset, endOffset });
      }
    }
    return entries;
  }

  function rangeForElementContents(element) {
    const range = document.createRange();
    range.selectNodeContents(element);
    return range;
  }

  function buildReplacementFragment(text, styleInfo, beforeDebug) {
    if (String(text).includes("\n") && shouldUseBlockFragment(beforeDebug)) {
      return buildBlockReplacementFragment(text, styleInfo);
    }

    const fragment = document.createDocumentFragment();
    const segments = Array.isArray(styleInfo.segments) ? styleInfo.segments : [];
    if (!segments.length) {
      appendTextWithBreaks(fragment, text);
      return fragment;
    }

    const normalized = normalizedSegmentsForText(segments, text);
    for (const segment of normalized) {
      const chunk = text.slice(segment.start, segment.end);
      if (!chunk) continue;
      const span = document.createElement("span");
      applyInlineStyle(span, segment.style || {});
      appendTextWithBreaks(span, chunk);
      fragment.appendChild(span);
    }
    return fragment;
  }

  function shouldUseBlockFragment(beforeDebug) {
    const html = String((beforeDebug || {}).htmlPreview || "");
    return /<\/?(p|div|li|h[1-6]|blockquote|pre)(\s|>|\/)/i.test(html);
  }

  function buildBlockReplacementFragment(text, styleInfo) {
    const fragment = document.createDocumentFragment();
    const lines = String(text).replace(/\r\n/g, "\n").replace(/\r/g, "\n").split("\n");
    const segments = Array.isArray(styleInfo.segments) ? normalizedSegmentsForText(styleInfo.segments, text) : [];
    let cursor = 0;

    lines.forEach((line) => {
      const paragraph = document.createElement("p");
      if (!line) {
        paragraph.appendChild(document.createElement("br"));
        fragment.appendChild(paragraph);
        cursor += 1;
        return;
      }

      appendStyledInlineRange(paragraph, text, cursor, cursor + line.length, segments);
      fragment.appendChild(paragraph);
      cursor += line.length + 1;
    });
    return fragment;
  }

  function appendStyledInlineRange(parent, text, start, end, segments) {
    const overlapping = segments.filter((segment) => segment.end > start && segment.start < end);
    if (!overlapping.length) {
      parent.appendChild(document.createTextNode(text.slice(start, end)));
      return;
    }

    let cursor = start;
    for (const segment of overlapping) {
      const segmentStart = Math.max(start, segment.start);
      const segmentEnd = Math.min(end, segment.end);
      if (segmentStart > cursor) {
        parent.appendChild(document.createTextNode(text.slice(cursor, segmentStart)));
      }
      const span = document.createElement("span");
      applyInlineStyle(span, segment.style || {});
      span.appendChild(document.createTextNode(text.slice(segmentStart, segmentEnd)));
      parent.appendChild(span);
      cursor = segmentEnd;
    }
    if (cursor < end) {
      parent.appendChild(document.createTextNode(text.slice(cursor, end)));
    }
  }

  function normalizedSegmentsForText(segments, text) {
    const result = [];
    let cursor = 0;
    for (let index = 0; index < segments.length; index += 1) {
      const source = segments[index] || {};
      const originalStart = Number.isFinite(source.start) ? source.start : parseInt(source.start || "0", 10);
      const originalEnd = Number.isFinite(source.end) ? source.end : parseInt(source.end || "0", 10);
      const sourceLength = Math.max(0, (originalEnd || 0) - (originalStart || 0));
      const start = cursor;
      const end = index === segments.length - 1 ? text.length : Math.min(text.length, cursor + sourceLength);
      cursor = end;
      if (end > start) {
        result.push({ start, end, style: source.style || {} });
      }
      if (cursor >= text.length) break;
    }
    if (!result.length && text) {
      result.push({ start: 0, end: text.length, style: segments[0] ? segments[0].style || {} : {} });
    }
    return result;
  }

  function appendTextWithBreaks(parent, text) {
    const lines = String(text).split("\n");
    lines.forEach((line, index) => {
      if (index > 0) parent.appendChild(document.createElement("br"));
      if (line) parent.appendChild(document.createTextNode(line));
    });
  }

  function applyInlineStyle(element, style) {
    const assignments = {
      fontFamily: "fontFamily",
      fontSize: "fontSize",
      fontWeight: "fontWeight",
      fontStyle: "fontStyle",
      color: "color",
      backgroundColor: "backgroundColor",
      textDecorationLine: "textDecorationLine"
    };
    for (const [source, target] of Object.entries(assignments)) {
      const value = style[source];
      if (typeof value === "string" && value && value !== "rgba(0, 0, 0, 0)") {
        element.style[target] = value;
      }
    }
  }

  document.addEventListener("selectionchange", scheduleCapture, true);
  document.addEventListener("input", scheduleSettledCaptures, true);
  document.addEventListener("keyup", scheduleSettledCaptures, true);
  document.addEventListener("mouseup", scheduleSettledCaptures, true);
  document.addEventListener("focusin", scheduleSettledCaptures, true);
  window.addEventListener("load", scheduleSettledCaptures, true);
  window.setTimeout(scheduleSettledCaptures, 500);
  window.setTimeout(scheduleSettledCaptures, 1500);
  window.setInterval(scheduleSettledCaptures, 3000);
  pollCommand();
})();
