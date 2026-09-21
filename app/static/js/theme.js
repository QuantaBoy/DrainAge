// Palette and light/dark switches, shared by every page. Both are one attribute on
// <html>; every colour token in home.css follows them.

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

    const toggle = document.getElementById("theme-toggle");
    const setDark = (on) => {
        root.dataset.theme = on ? "dark" : "light";
        toggle.setAttribute("aria-pressed", String(on));
    };
    toggle.addEventListener("click", () => {
        const dark = root.dataset.theme !== "dark";
        setDark(dark);
        remember("theme", dark ? "dark" : "light");
    });
    setDark(root.dataset.theme === "dark");
})();
