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
  let lastInteractedEditable = null;
  let lastInteractedAt = 0;
  let observedEditable = null;
  let editableObserver = null;

  const GMAIL_EDITOR_SELECTOR = [
    ".Am.Al.editable",
    ".editable[contenteditable='true']",
    "[g_editable='true']",
    "[aria-label='Message Body']",
    "[aria-label*='Message Body']",
    "[aria-label*='메일 본문']",
    "[aria-label*='메시지 본문']"
  ].join(",");

  const NAVER_CAFE_EDITOR_SELECTOR = [
    ".se-editable",
    ".se-editable[contenteditable='true']",
    ".se-component-content",
    ".se-content",
    ".se-main-container",
    ".se-module-text",
    ".se-text-paragraph",
    ".se-section-text",
    ".se2_inputarea",
    ".SmartEditor",
    "[class*='SmartEditor']",
    "[id*='SmartEditor']",
    "[data-a11y-title*='본문']",
    "[data-placeholder*='본문']",
    "[data-placeholder*='내용']"
  ].join(",");

  const EDITABLE_SELECTOR = [
    "textarea",
    "input[type='text']",
    "input[type='search']",
    "input[type='email']",
    "input:not([type])",
    "[contenteditable]",
    "[contenteditable='']",
    "[contenteditable='true']",
    "[role='textbox']",
    "[aria-label]",
    "[placeholder]",
    ".Am.Al.editable",
    ".editable[contenteditable='true']",
    ".se-editable",
    ".se-content",
    ".se-component-content",
    "[class*='SmartEditor']",
    "[id*='SmartEditor']",
    GMAIL_EDITOR_SELECTOR,
    NAVER_CAFE_EDITOR_SELECTOR
  ].join(",");

  const EDITOR_HINT_RE = /(\uae00\uc4f0\uae30|\ub0b4\uc6a9|\ubcf8\ubb38|\uba54\uc2dc\uc9c0|\ub313\uae00|\ub2f5\uae00|\uac8c\uc2dc\uae00|\uba54\uc77c|\uce74\ud398|\uc5d0\ub514\ud130|compose|message|body|write|editor|content|textbox|mail|cafe|smarteditor|article|post|reply|comment)/i;

  console.info("[Writing Assistant Bridge] content script loaded", {
    sessionId: SESSION_ID,
    url: location.href
  });

  function isEditableElement(node) {
    if (!node || node.nodeType !== Node.ELEMENT_NODE) return false;
    const tag = node.tagName.toLowerCase();
    return tag === "textarea" || isTextInputElement(node) || isEditorLikeElement(node);
  }

  function isTextInputElement(node) {
    if (!node || node.nodeType !== Node.ELEMENT_NODE) return false;
    if (node.tagName.toLowerCase() !== "input") return false;
    const type = String(node.getAttribute("type") || "text").toLowerCase();
    return ["text", "search", "email"].includes(type);
  }

  function isEditorLikeElement(node) {
    if (!node || node.nodeType !== Node.ELEMENT_NODE) return false;
    if (node.isContentEditable) return true;
    if (node.getAttribute("role") === "textbox") return true;
    if (EDITOR_HINT_RE.test(editorHintText(node))) return true;
    return false;
  }

  function editorHintText(element) {
    if (!element || !element.getAttribute) return "";
    return [
      element.id || "",
      typeof element.className === "string" ? element.className : "",
      element.getAttribute("role") || "",
      element.getAttribute("aria-label") || "",
      element.getAttribute("placeholder") || "",
      element.getAttribute("title") || "",
      element.getAttribute("name") || ""
    ].join(" ").toLowerCase();
  }

  function closestMatches(element, selector) {
    try {
      return Boolean(element && element.closest && element.closest(selector));
    } catch (_error) {
      return false;
    }
  }

  function pageHintText() {
    return `${location.hostname || ""} ${location.href || ""} ${document.title || ""}`.toLowerCase();
  }

  function closestEditableFromNode(node) {
    if (!node) return null;
    const element = node.nodeType === Node.TEXT_NODE ? node.parentElement : node;
    if (!element || !element.closest) return null;
    if (isEditorChromeNode(element)) return null;
    return element.closest(EDITABLE_SELECTOR);
  }

  function activeEditable() {
    const recent = recentInteractedEditable();
    if (recent) return chooseEditable(recent);

    const selection = window.getSelection();
    if (selection && selection.rangeCount > 0) {
      const editableFromSelection = closestEditableFromNode(selection.anchorNode);
      if (isCaptureReadyEditable(editableFromSelection)) {
        return chooseEditable(editableFromSelection);
      }
    }

    const active = document.activeElement;
    if (isCaptureReadyEditable(active)) {
      return chooseEditable(active);
    }

    const fallback = bestEditableCandidate();
    if (fallback) {
      return chooseEditable(fallback);
    }
    return null;
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

  function rememberInteractedEditable(target) {
    const editable = closestEditableFromNode(target) || (isCaptureReadyEditable(target) ? target : null);
    if (!editable || !isCaptureReadyEditable(editable)) return;
    lastInteractedEditable = editable;
    lastInteractedAt = Date.now();
    chooseEditable(editable, "interaction");
  }

  function chooseEditable(element, reason = "selected") {
    if (!element) return null;
    lastEditable = element;
    observeEditable(element);
    logSelectedEditable(element, reason);
    return element;
  }

  function logSelectedEditable(element, reason) {
    if (!element) return;
    const rect = element.getBoundingClientRect();
    const className = typeof element.className === "string" ? element.className.slice(0, 140) : "";
    console.log("[Writing Assistant Bridge] selected editable", {
      reason,
      tagName: element.tagName,
      role: element.getAttribute("role") || "",
      contenteditable: element.getAttribute("contenteditable") || String(Boolean(element.isContentEditable)),
      className,
      id: element.id || "",
      ariaLabel: element.getAttribute("aria-label") || "",
      placeholder: element.getAttribute("placeholder") || "",
      width: Math.round(rect.width),
      height: Math.round(rect.height),
      frameUrl: location.href
    });
  }

  function recentInteractedEditable() {
    if (!isCaptureReadyEditable(lastInteractedEditable)) return null;
    if (Date.now() - lastInteractedAt > 10 * 60 * 1000) return null;
    return lastInteractedEditable;
  }

  function bestEditableCandidate() {
    const candidates = Array.from(document.querySelectorAll(EDITABLE_SELECTOR))
      .filter((element) => isCaptureReadyEditable(element) && editableCandidateText(element).trim());
    if (!candidates.length) return null;
    if (candidates.length === 1) return candidates[0];
    return candidates.sort((left, right) => {
      const leftScore = editableCandidateScore(left);
      const rightScore = editableCandidateScore(right);
      return rightScore - leftScore;
    })[0];
  }

  function editableCandidateScore(element) {
    const rect = element.getBoundingClientRect();
    const viewportCenterX = window.innerWidth / 2;
    const viewportCenterY = window.innerHeight / 2;
    const elementCenterX = rect.left + rect.width / 2;
    const elementCenterY = rect.top + rect.height / 2;
    const distance = Math.hypot(elementCenterX - viewportCenterX, elementCenterY - viewportCenterY);
    const focusBoost = element === document.activeElement ? 900 : 0;
    const recentBoost = element === lastInteractedEditable ? 1200 : 0;
    return (
      candidatePriority(element) * 10000 +
      editableCandidateText(element).length +
      visibleArea(element) / 80 +
      editorLikeScore(element) +
      focusBoost +
      recentBoost -
      distance / 4
    );
  }

  function candidatePriority(element) {
    if (!element || !element.matches) return 0;
    const hint = editorHintText(element);
    if (isGmailEditor(element)) return 95;
    if (isCafeEditor(element)) return 90;
    if (element.matches("textarea")) return 80;
    if (isTextInputElement(element)) return 70;
    if (element.isContentEditable || element.getAttribute("contenteditable") === "true") return 60;
    if (element.getAttribute("role") === "textbox") return 50;
    if (EDITOR_HINT_RE.test(hint)) return 40;
    return 10;
  }

  function isGmailEditor(element) {
    const hint = editorHintText(element);
    const page = pageHintText();
    return (
      /mail\.google\.com|gmail/.test(page) && closestMatches(element, GMAIL_EDITOR_SELECTOR)
    ) || /gmail|g_editable|message body|\uba54\uc77c \ubcf8\ubb38|\uba54\uc2dc\uc9c0 \ubcf8\ubb38/.test(hint);
  }

  function isCafeEditor(element) {
    const hint = editorHintText(element);
    const page = pageHintText();
    return (
      /cafe\.naver\.com|m\.cafe\.naver\.com|naver.*cafe/.test(page) && closestMatches(element, NAVER_CAFE_EDITOR_SELECTOR)
    ) || /se-|smarteditor|cafe|article|post|editor|\ub313\uae00|\ub2f5\uae00|\ubcf8\ubb38|\ub0b4\uc6a9/.test(hint);
  }

  function isVisibleEditable(element) {
    if (!element) return false;
    const rect = element.getBoundingClientRect();
    const style = window.getComputedStyle(element);
    return rect.width > 0 && rect.height > 0 && style.visibility !== "hidden" && style.display !== "none" && style.opacity !== "0";
  }

  function isCaptureReadyEditable(element) {
    return Boolean(isEditableElement(element) && isVisibleEditable(element) && !isTransientInputBuffer(element));
  }

  function isTransientInputBuffer(element) {
    if (!element || !element.matches) return true;
    if (element.matches("[data-input-buffer]")) return true;
    const style = window.getComputedStyle(element);
    const inlineStyle = element.getAttribute("style") || "";
    const transformText = `${style.transform || ""} ${inlineStyle}`.toLowerCase();
    if (transformText.includes("rotatex(90deg)")) return true;
    if (!element.querySelector("[data-input-buffer]")) return false;
    return !editorContentText(element).trim();
  }

  function editorContentText(element) {
    if (!element) return "";
    if (element.matches && (element.matches("textarea") || isTextInputElement(element))) return element.value || "";
    const chunks = [];
    const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT, {
      acceptNode(node) {
        return isEditorContentNode(node, element) && (node.nodeValue || "").trim()
          ? NodeFilter.FILTER_ACCEPT
          : NodeFilter.FILTER_REJECT;
      }
    });
    while (walker.nextNode()) {
      chunks.push(walker.currentNode.nodeValue || "");
    }
    return chunks.join("");
  }

  function editorLikeScore(element) {
    const attrs = editorHintText(element);
    let score = 0;
    if (EDITOR_HINT_RE.test(attrs)) score += 500;
    if (/gmail|editable|g_editable|se-|prosemirror|article|post|cafe|smarteditor/.test(attrs)) score += 500;
    if (isGmailEditor(element) || isCafeEditor(element)) score += 900;
    if (element.getAttribute("role") === "textbox") score += 300;
    if (element.matches("textarea") || isTextInputElement(element)) score += 200;
    if (element.isContentEditable) score += 250;
    return score;
  }

  function editableCandidateText(element) {
    if (!element) return "";
    if (element.matches("textarea") || isTextInputElement(element)) return element.value || "";
    return editorContentText(element) || element.textContent || "";
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
      target_kind: isTextInputElement(element) ? "input" : element.tagName.toLowerCase(),
      selection: {
        start,
        end,
        selected: hasSelection
      },
      segments: hasSelection ? [{ start: 0, end: text.length, style: styleFromElement(element) }] : []
    };
  }

  function captureContentEditable(element) {
    const range = selectedRangeInside(element);
    const elementText = editablePlainText(element);
    const hasSelection = Boolean(range);
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
      if (!isEditorContentNode(block, element) || !blockHasEditorText(block, element)) return false;
      const nestedBlocks = Array.from(block.querySelectorAll(blockSelector));
      return !nestedBlocks.some((nested) => nested !== block && isEditorContentNode(nested, element) && blockHasEditorText(nested, element));
    });
  }

  function blockPlainText(block) {
    if (isEmptyBlock(block)) return "";
    return inlinePlainText(block);
  }

  function isEmptyBlock(block) {
    return !String(block.textContent || "").trim() && Boolean(block.querySelector("br") || block.children.length === 0);
  }

  function inlinePlainText(element) {
    const chunks = [];
    walkInlineText(element, chunks, element);
    return chunks.join("").replace(/\r\n/g, "\n").replace(/\r/g, "\n").replace(/\n+$/g, "");
  }

  function walkInlineText(node, chunks, root) {
    if (!isEditorContentNode(node, root)) return;
    if (node.nodeType === Node.TEXT_NODE) {
      chunks.push(node.nodeValue || "");
      return;
    }
    if (node.nodeType !== Node.ELEMENT_NODE) return;
    if (node.tagName && node.tagName.toLowerCase() === "br") {
      chunks.push("\n");
      return;
    }
    Array.from(node.childNodes).forEach((child) => walkInlineText(child, chunks, root || node));
  }

  function isEditorContentNode(node, root) {
    if (!node) return false;
    const element = node.nodeType === Node.TEXT_NODE ? node.parentElement : node;
    if (!element) return false;
    if (root && element !== root && !root.contains(element)) return false;
    if (element === root) return true;
    return !isEditorChromeNode(element);
  }

  function isEditorChromeNode(element) {
    if (!element || !element.closest) return false;
    return Boolean(element.closest(
      "[data-input-buffer],[contenteditable='false'],[aria-hidden='true'],button,input,select,textarea,script,style,noscript," +
      "[role='button'],[role='toolbar'],[role='menu'],[role='menubar'],[role='menuitem'],[role='tab'],[role='switch']"
    ));
  }

  function blockHasEditorText(block, root) {
    const walker = document.createTreeWalker(block, NodeFilter.SHOW_TEXT, {
      acceptNode(node) {
        return isEditorContentNode(node, root) && (node.nodeValue || "").trim()
          ? NodeFilter.FILTER_ACCEPT
          : NodeFilter.FILTER_REJECT;
      }
    });
    return Boolean(walker.nextNode()) || isEmptyBlock(block);
  }

  function domDebug(element, range) {
    const textNodes = [];
    const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT, {
      acceptNode(node) {
        return isEditorContentNode(node, element) && (node.nodeValue || "").trim() ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_SKIP;
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
        return isEditorContentNode(node, fallbackElement) && range.intersectsNode(node) ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT;
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
    const blockSelector = "p,div,li,h1,h2,h3,h4,h5,h6,blockquote,pre";
    const blocks = leafTextBlocks(element, blockSelector);
    if (blocks.length) {
      const segments = [];
      let cursor = 0;
      blocks.forEach((block, index) => {
        const blockText = blockPlainText(block);
        appendSegmentsForContainer(segments, block, blockText, cursor, styleFromElement(block));
        cursor += blockText.length;
        if (index < blocks.length - 1) cursor += 1;
      });
      return completeSegments(segments, text, styleFromElement(element));
    }

    const segments = [];
    appendSegmentsForContainer(segments, element, text, 0, styleFromElement(element));
    return completeSegments(segments, text, styleFromElement(element));
  }

  function appendSegmentsForContainer(segments, container, containerText, offset, fallbackStyle) {
    if (!containerText) return;
    const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT, {
      acceptNode(node) {
        return isEditorContentNode(node, container) && (node.nodeValue || "").length ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_SKIP;
      }
    });

    let position = 0;
    while (walker.nextNode()) {
      const node = walker.currentNode;
      const chunk = node.nodeValue || "";
      if (!chunk) continue;
      const foundAt = containerText.indexOf(chunk, position);
      const localStart = foundAt >= 0 ? foundAt : position;
      const localEnd = Math.min(containerText.length, localStart + chunk.length);
      if (localEnd <= localStart) continue;
      appendSegment(segments, offset + localStart, offset + localEnd, styleFromNode(node, fallbackStyle));
      position = localEnd;
    }
  }

  function completeSegments(segments, text, fallbackStyle) {
    if (!segments.length) {
      return [{ start: 0, end: text.length, style: fallbackStyle }];
    }
    if (segments[0].start > 0) {
      segments.unshift({ start: 0, end: segments[0].start, style: fallbackStyle });
    }
    const last = segments[segments.length - 1];
    if (last.end < text.length) {
      appendSegment(segments, last.end, text.length, fallbackStyle);
    }
    return segments;
  }

  function styleFromNode(node, fallbackStyle) {
    const parent = node && node.parentElement;
    if (!parent) return fallbackStyle || {};
    return styleFromElement(parent);
  }

  function styleFromElement(element) {
    const computed = window.getComputedStyle(element);
    const decoration = effectiveTextDecoration(element, computed);
    const textColor = effectiveTextColor(element, computed);
    return {
      fontFamily: computed.fontFamily,
      fontSize: computed.fontSize,
      fontWeight: computed.fontWeight,
      fontStyle: computed.fontStyle,
      color: textColor,
      webkitTextFillColor: textColor,
      verticalAlign: effectiveVerticalAlign(element, computed),
      textDecorationLine: decoration.line,
      textDecorationColor: decoration.color,
      textDecorationStyle: decoration.style,
      textDecorationThickness: decoration.thickness,
      backgroundColor: effectiveBackgroundColor(element, computed)
    };
  }

  function effectiveTextDecoration(element, computed) {
    const lines = [];
    let color = computed.textDecorationColor;
    let style = computed.textDecorationStyle;
    let thickness = computed.textDecorationThickness;
    let current = element;
    while (current && current.nodeType === Node.ELEMENT_NODE) {
      const currentStyle = current === element ? computed : window.getComputedStyle(current);
      const line = currentStyle.textDecorationLine || "none";
      if (line && line !== "none") {
        line.split(/\s+/).forEach((part) => {
          if (part && part !== "none" && !lines.includes(part)) lines.push(part);
        });
        color = currentStyle.textDecorationColor || color;
        style = currentStyle.textDecorationStyle || style;
        thickness = currentStyle.textDecorationThickness || thickness;
      }
      current = current.parentElement;
    }
    return {
      line: lines.length ? lines.join(" ") : "none",
      color,
      style,
      thickness
    };
  }

  function effectiveTextColor(element, computed) {
    const fill = computed.webkitTextFillColor;
    if (fill && fill !== "transparent" && fill !== "rgba(0, 0, 0, 0)") {
      return fill;
    }
    return computed.color;
  }

  function effectiveVerticalAlign(element, computed) {
    let current = element;
    while (current && current.nodeType === Node.ELEMENT_NODE) {
      const tag = current.tagName ? current.tagName.toLowerCase() : "";
      if (tag === "sup") return "super";
      if (tag === "sub") return "sub";
      const style = current === element ? computed : window.getComputedStyle(current);
      const align = style.verticalAlign || "baseline";
      if (align && align !== "baseline" && align !== "normal" && align !== "0px") {
        return align;
      }
      current = current.parentElement;
    }
    return computed.verticalAlign;
  }

  function effectiveBackgroundColor(element, computed) {
    let current = element;
    while (current && current.nodeType === Node.ELEMENT_NODE) {
      const style = current === element ? computed : window.getComputedStyle(current);
      const color = style.backgroundColor;
      if (color && color !== "transparent" && color !== "rgba(0, 0, 0, 0)") {
        return color;
      }
      current = current.parentElement;
    }
    return computed.backgroundColor;
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
    if (!element || element.matches("textarea") || observedEditable === element) return;
    if (editableObserver) editableObserver.disconnect();
    observedEditable = element;
    editableObserver = new MutationObserver(() => scheduleCapture());
    editableObserver.observe(element, {
      childList: true,
      characterData: true,
      subtree: true
    });
  }

  function shouldCaptureDocument() {
    if (isCaptureReadyEditable(document.activeElement)) return true;
    if (isCaptureReadyEditable(lastInteractedEditable)) return true;
    const selection = window.getSelection();
    if (selection && selection.rangeCount > 0) {
      const editableFromSelection = closestEditableFromNode(selection.anchorNode);
      if (isCaptureReadyEditable(editableFromSelection)) {
        return true;
      }
    }
    return document.hasFocus();
  }

  async function bridgeRequest(path, options = {}) {
    if (typeof chrome !== "undefined" && chrome.runtime && chrome.runtime.sendMessage) {
      try {
        const response = await chrome.runtime.sendMessage({
          source: "writing-assistant-content",
          type: "bridgeFetch",
          path,
          method: options.method || "GET",
          headers: options.headers || { "Content-Type": "application/json" },
          body: options.body
        });
        if (response && response.ok) {
          return response.result || { ok: false, status: 0, data: null };
        }
      } catch (_error) {
        // Fallback to direct fetch below.
      }
    }

    const response = await fetch(`${BRIDGE}${path}`, options);
    const data = await response.json();
    return { ok: response.ok, status: response.status, data };
  }

  async function captureNow() {
    if (!shouldCaptureDocument()) return;
    const element = activeEditable();
    if (!element) return;
    if (!shouldCaptureDocument()) return;
    const payload = payloadFromElement(element);
    if (!payload.text.trim()) return;

    const signature = JSON.stringify([payload.url, payload.text, payload.html, payload.selection, payload.segments]);
    const now = Date.now();
    if (signature === lastSignature && now - lastCaptureSentAt < 5000) return;

    try {
      const response = await bridgeRequest("/capture", {
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
      const response = await bridgeRequest(`/command?session_id=${encodeURIComponent(SESSION_ID)}`);
      const data = response.data;
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
    if (element.matches("textarea") || isTextInputElement(element)) {
      const start = 0;
      const end = element.value.length;
      element.setRangeText(text, start, end, "end");
      element.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "insertReplacementText", data: text }));
      element.dispatchEvent(new Event("change", { bubbles: true }));
      return;
    }

    const selection = window.getSelection();
    const range = rangeForElementContents(element);
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

  function payloadFromElement(element) {
    const payload = (element.matches("textarea") || isTextInputElement(element))
      ? captureInput(element)
      : captureContentEditable(element);
    payload.segments = cleanSegments(payload.segments || []);
    payload.session_id = SESSION_ID;
    payload.url = location.href;
    payload.title = document.title;
    return payload;
  }

  function focusedTextPayload() {
    const element = activeEditable();
    if (!element) {
      return {
        ok: false,
        error: "Focused editable field was not found."
      };
    }
    const payload = payloadFromElement(element);
    if (!String(payload.text || "").trim()) {
      return {
        ok: false,
        error: "The editable field is empty.",
        payload
      };
    }
    return { ok: true, payload };
  }

  async function reportApplied(method, text, before, after) {
    try {
      await bridgeRequest("/applied", {
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
    if (range.toString().length !== String(text).length) return false;
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
        return isEditorContentNode(node, element) && range.intersectsNode(node) ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT;
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
    let cursor = 0;
    for (const segment of normalized) {
      if (segment.start > cursor) {
        appendTextWithBreaks(fragment, text.slice(cursor, segment.start));
      }
      const chunk = text.slice(segment.start, segment.end);
      if (chunk) {
        const span = document.createElement("span");
        applyInlineStyle(span, segment.style || {});
        appendTextWithBreaks(span, chunk);
        fragment.appendChild(span);
      }
      cursor = Math.max(cursor, segment.end);
    }
    if (cursor < String(text || "").length) {
      appendTextWithBreaks(fragment, text.slice(cursor));
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
    const length = String(text || "").length;
    const result = [];
    const sorted = (Array.isArray(segments) ? segments : [])
      .map((source) => {
        const start = Number.isFinite(source.start) ? source.start : parseInt(source.start || "0", 10);
        const end = Number.isFinite(source.end) ? source.end : parseInt(source.end || "0", 10);
        return {
          start: Math.max(0, Math.min(length, start || 0)),
          end: Math.max(0, Math.min(length, end || 0)),
          style: source.style || {}
        };
      })
      .filter((segment) => segment.end > segment.start)
      .sort((left, right) => left.start - right.start || left.end - right.end);

    for (const segment of sorted) {
      const previous = result[result.length - 1];
      const start = previous ? Math.max(segment.start, previous.end) : segment.start;
      if (segment.end <= start) continue;
      splitSegmentByLine(result, text, start, segment.end, segment.style);
    }

    return result;
  }

  function splitSegmentByLine(result, text, start, end, style) {
    let cursor = start;
    while (cursor < end) {
      const newline = String(text || "").indexOf("\n", cursor);
      const segmentEnd = newline >= 0 && newline < end ? newline : end;
      if (segmentEnd > cursor) {
        result.push({ start: cursor, end: segmentEnd, style });
      }
      if (newline < 0 || newline >= end) break;
      cursor = newline + 1;
    }
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
      webkitTextFillColor: "webkitTextFillColor",
      verticalAlign: "verticalAlign",
      textDecorationLine: "textDecorationLine",
      textDecorationColor: "textDecorationColor",
      textDecorationStyle: "textDecorationStyle",
      textDecorationThickness: "textDecorationThickness"
    };
    for (const [source, target] of Object.entries(assignments)) {
      const value = style[source];
      if (typeof value === "string" && value && value !== "rgba(0, 0, 0, 0)") {
        element.style[target] = value;
      }
    }
  }

  if (typeof chrome !== "undefined" && chrome.runtime && chrome.runtime.onMessage) {
    chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
      if (!message || message.source !== "writing-assistant-popup") return false;

      if (message.type === "getFocusedText") {
        sendResponse(focusedTextPayload());
        return true;
      }

      if (message.type === "applyText") {
        const text = String(message.text || "");
        if (!text.trim()) {
          sendResponse({ ok: false, error: "No correction result to apply." });
          return true;
        }
        applyReplacement(text, message.style_info || {});
        scheduleCapture();
        sendResponse({ ok: true });
        return true;
      }

      return false;
    });
  }

  document.addEventListener("mousedown", (event) => rememberInteractedEditable(event.target), true);
  document.addEventListener("click", (event) => rememberInteractedEditable(event.target), true);
  document.addEventListener("focusin", (event) => {
    rememberInteractedEditable(event.target);
    scheduleSettledCaptures();
  }, true);
  document.addEventListener("input", (event) => {
    rememberInteractedEditable(event.target);
    scheduleSettledCaptures();
  }, true);
  document.addEventListener("keyup", (event) => {
    rememberInteractedEditable(event.target);
    scheduleSettledCaptures();
  }, true);
  document.addEventListener("selectionchange", scheduleCapture, true);
  document.addEventListener("mouseup", scheduleSettledCaptures, true);
  window.addEventListener("load", scheduleCapture, true);
  pollCommand();
})();
