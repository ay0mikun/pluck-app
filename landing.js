const launchReducedMotion = matchMedia("(prefers-reduced-motion: reduce)");

document.querySelectorAll("[data-get-started]").forEach((button) => {
  button.addEventListener("click", () => {
    location.hash = "auth";
  });
});

document.querySelectorAll("[data-coming-soon]").forEach((button) => {
  let shake;

  button.addEventListener("click", () => {
    shake?.cancel();
    if (launchReducedMotion.matches) return;

    shake = button.animate(
      ["0", "-4px", "3px", "-2px", "1px", "0"].map((translate) => ({
        translate,
      })),
      { duration: 260, easing: "ease-out" },
    );
  });
});
