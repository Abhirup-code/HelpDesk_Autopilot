// Show only the fields that belong to the chosen request type.
// Without JavaScript every section stays visible and the server ignores the extras.
document.addEventListener("DOMContentLoaded", () => {
  const type = document.getElementById("type");
  if (!type) return;
  const sections = document.querySelectorAll("fieldset[data-for]");
  const sync = () => {
    sections.forEach((fs) => {
      const active = fs.dataset.for === type.value;
      fs.hidden = !active;
      fs.querySelectorAll("input, select").forEach((el) => { el.required = active; });
    });
  };
  type.addEventListener("change", sync);
  sync();
});
