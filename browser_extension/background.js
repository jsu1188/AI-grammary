const BRIDGE = "http://127.0.0.1:8766";

async function bridgeFetch(message) {
  const path = String(message.path || "");
  if (!path.startsWith("/")) {
    throw new Error("Invalid bridge path.");
  }
  const method = String(message.method || "GET").toUpperCase();
  const headers = Object.assign({ "Content-Type": "application/json" }, message.headers || {});
  const options = { method, headers };
  if (message.body !== undefined) {
    options.body = typeof message.body === "string" ? message.body : JSON.stringify(message.body);
  }

  const response = await fetch(`${BRIDGE}${path}`, options);
  const text = await response.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch (_error) {
    data = text;
  }
  return {
    ok: response.ok,
    status: response.status,
    data
  };
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (!message || message.source !== "writing-assistant-content" || message.type !== "bridgeFetch") {
    return false;
  }

  bridgeFetch(message)
    .then((result) => sendResponse({ ok: true, result }))
    .catch((error) =>
      sendResponse({
        ok: false,
        error: error && error.message ? error.message : "Bridge request failed.",
      })
    );
  return true;
});
