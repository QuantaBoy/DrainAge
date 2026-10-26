// The palette switch, shared by every page: one attribute on <html> that every colour
// token in home.css follows. The site is light only.

(() => {
    const root = document.documentElement;
    const remember = (key, value) => { try { localStorage.setItem(key, value); } catch (err) {} };

    const buttons = document.querySelectorAll(".palette button");
    const paint = (name) => {
        root.dataset.palette = name;
        for (const button of buttons) {
            button.setAttribute("aria-pressed", String(button.dataset.palette === name));
        }
    };
    for (const button of buttons) {
        button.addEventListener("click", () => {
            paint(button.dataset.palette);
            remember("palette", button.dataset.palette);
        });
    }
    paint(root.dataset.palette);
})();
