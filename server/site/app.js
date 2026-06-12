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
