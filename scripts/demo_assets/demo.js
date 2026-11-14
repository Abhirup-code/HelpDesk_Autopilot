// Click-through demo: every page is a saved screen from the real portal. Clicking the highlighted
// control (or Next) opens the next screen; nothing here talks to a server.
document.addEventListener("DOMContentLoaded", () => {
  const next = document.querySelector(".demo-next");
  const back = document.querySelector(".demo-back");
  const hint = document.querySelector(".demo-hint");
  const hot = document.querySelector("[data-demo-hot]");
  let timer;

  const go = (link) => {
    if (link && link.getAttribute("aria-disabled") !== "true") window.location.href = link.href;
  };
  if (hot && hot !== next) hot.scrollIntoView({ block: "center" });

  document.addEventListener("click", (event) => {
    if (event.target.closest(".demo-bar")) return; // the bar's own links work normally
    const control = event.target.closest("a, button, input, select, textarea, label");
    if (!control) return;
    event.preventDefault();
    if (hot && hot.contains(event.target)) return go(next);
    hint.hidden = false;
    clearTimeout(timer);
    timer = setTimeout(() => { hint.hidden = true; }, 2200);
  });
  document.addEventListener("submit", (event) => event.preventDefault());
  document.addEventListener("keydown", (event) => {
    if (event.key === "ArrowRight") go(next);
    if (event.key === "ArrowLeft") go(back);
  });
});
