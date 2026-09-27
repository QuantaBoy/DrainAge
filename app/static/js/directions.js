// The directions bar at the top of the map, the way a maps app puts it first: where
// from, where to, when you leave, Go. It drives the Route panel's own form (route.js),
// so there is one routing path; the full result opens in the Route tab.

(() => {
    const bar = document.getElementById("dir-bar");
    if (!bar) return;
    const from = document.getElementById("dir-from");
    const to = document.getElementById("dir-to");
    const leave = document.getElementById("dir-leave");
    const swap = document.getElementById("dir-swap");

    // The same departure times the Route panel offers.
    for (const option of routeUi.leave.options) leave.append(new Option(option.text, option.value));

    // An empty start means "where I am", when the map knows where that is.
    const MY_LOCATION = "Your location";
    function fill(field, text, allowHere) {
        if (!text && allowHere && typeof marker !== "undefined" && marker) {
            const { lat, lng } = marker.getLatLng();
            field.input.value = MY_LOCATION;
            setPoint(field, lat, lng, MY_LOCATION);
            return;
        }
        if (field.input.value === text && field.point) return;   // already resolved
        field.input.value = text;
        field.input.dispatchEvent(new Event("input"));           // forget the old place
    }

    function go(event) {
        event?.preventDefault();
        if (!to.value.trim()) { to.focus(); return; }
        fill(routeUi.from, from.value.trim(), true);
        fill(routeUi.to, to.value.trim(), false);
        routeUi.leave.value = leave.value;
        routeUi.go.click();
        bar.classList.remove("open");
        to.blur(); from.blur();
    }
    bar.addEventListener("submit", go);

    swap.addEventListener("click", () => {
        [from.value, to.value] = [to.value, from.value];
        to.focus();
    });

    // Places picked on the map, or typed in the Route panel, show up here too.
    const mirror = () => {
        if (document.activeElement !== from) from.value = routeUi.from.input.value;
        if (document.activeElement !== to) to.value = routeUi.to.input.value;
        leave.value = routeUi.leave.value;
    };
    for (const field of [routeUi.from, routeUi.to]) field.input.addEventListener("input", mirror);
    routeUi.go.addEventListener("click", () => setTimeout(mirror, 0));
    routeUi.leave.addEventListener("change", mirror);

    // On a phone the bar is one "Where to?" box until it is used.
    bar.addEventListener("focusin", () => bar.classList.add("open"));
    document.addEventListener("pointerdown", (event) => {
        if (!bar.contains(event.target)) bar.classList.remove("open");
    });
})();
