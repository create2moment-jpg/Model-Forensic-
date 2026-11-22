/** "If it does not fit, show it on hover" — everywhere. When the pointer enters an element whose text is cut
 *  (``text-overflow: ellipsis`` or any overflow), and it has no title of its own, the full text becomes its tooltip.
 *  Installed once by the layout; costs one check per hovered element. */
function clipped(el: HTMLElement): boolean {
  return el.scrollWidth > el.clientWidth + 1 || el.scrollHeight > el.clientHeight + 1;
}

function onOver(ev: MouseEvent): void {
  let el = ev.target as HTMLElement | null;
  for (let i = 0; el && i < 3; i++, el = el.parentElement) {  // the hovered element or a close ancestor
    if (el.hasAttribute("title") && !el.dataset.autoTitle) return;
    const style = getComputedStyle(el);
    if ((style.textOverflow === "ellipsis" || style.overflow === "hidden") && clipped(el)) {
      const text = (el.innerText || el.textContent || "").trim();
      if (text) {
        el.setAttribute("title", text);
        el.dataset.autoTitle = "1";
      }
      return;
    }
  }
}

let installed = false;
export function installAutoTitle(): void {
  if (installed || typeof document === "undefined") return;
  installed = true;
  document.addEventListener("mouseover", onOver, { passive: true });
}
