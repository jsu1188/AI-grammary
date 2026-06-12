document.querySelectorAll('a[href^="#"]').forEach((link) => {
  link.addEventListener("click", (event) => {
    const href = link.getAttribute("href");
    if (!href || href === "#") {
      return;
    }
    const target = document.querySelector(href);
    if (!target) {
      return;
    }
    event.preventDefault();
    target.scrollIntoView({ behavior: "smooth", block: "start" });
  });
});

const downloadLinks = [
  document.getElementById("hero-download-link"),
  document.getElementById("main-download-link"),
].filter(Boolean);

fetch("/client-version")
  .then((response) => {
    if (!response.ok) {
      throw new Error("Failed to load client version info.");
    }
    return response.json();
  })
  .then((data) => {
    const latestVersion = String(data.latest_version || "").trim();
    const downloadUrl = String(data.download_url || "").trim();
    if (!downloadUrl) {
      return;
    }
    downloadLinks.forEach((link) => {
      link.href = downloadUrl;
      if (latestVersion && link.id === "main-download-link") {
        link.textContent = `Checkword ${latestVersion} 다운로드`;
      }
    });
  })
  .catch(() => {
    // Keep the fallback local download path when version metadata is unavailable.
  });
