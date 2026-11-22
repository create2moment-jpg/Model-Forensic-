/** Click-to-sort for every data table (``table.t`` with a ``<thead>``): first click ascending, second descending,
 *  third back to the original order. Values are read from ``data-sort`` when a cell has it, else from its text, and
 *  understood as numbers when they look like one (``12 %``, ``1.2 GB``, ``350 ms``, ``−0.03``, ``×1.4``, ``2.5e-4``);
 *  empty cells (``—``) always go last. A table that sorts itself (``th.sortable``), opts out (``data-nosort``) or has
 *  detail rows (``td[colspan]``) is left alone. When the app re-renders the rows, the chosen order is re-applied. */

const UNITS: Record<string, number> = {
  b: 1, kb: 1e3, mb: 1e6, gb: 1e9, tb: 1e12, kib: 1024, mib: 1024 ** 2, gib: 1024 ** 3,
  ms: 1e-3, s: 1, sec: 1, min: 60, h: 3600, d: 86400, k: 1e3, m: 1e6,
};
const EMPTY = new Set(["", "—", "-", "–", "n/a", "none", "null"]);

export function sortValue(raw: string): number | string | null {
  const t = raw.replace(/[   ]/g, " ").trim();
  if (EMPTY.has(t.toLowerCase())) return null;
  const m = /^([+\-−×x]?)\s*(\d[\d,]*(?:\.\d+)?(?:e[+-]?\d+)?|\.\d+)\s*(%|[a-zA-Z]{1,3})?\b/.exec(t);
  if (m) {
    let v = Number(m[2]!.replace(/,/g, ""));
    if (m[1] === "-" || m[1] === "−") v = -v;
    const u = (m[3] ?? "").toLowerCase();
    if (u && u !== "%" && UNITS[u] !== undefined) v *= UNITS[u]!;
    if (Number.isFinite(v)) return v;
  }
  return t.toLowerCase();
}

function compare(a: number | string | null, b: number | string | null): number {
  if (a === null || b === null) return a === b ? 0 : a === null ? 1 : -1;
  if (typeof a === "number" && typeof b === "number") return a - b;
  return String(a).localeCompare(String(b), undefined, { numeric: true });
}

interface State { col: number; dir: 1 | -1; observer: MutationObserver }
const states = new WeakMap<HTMLTableSectionElement, State>();
const original = new WeakMap<HTMLTableRowElement, number>();

function eligible(table: HTMLTableElement): boolean {
  return table.classList.contains("t") && !table.hasAttribute("data-nosort") && !!table.tHead && table.tBodies.length > 0
    && !table.tBodies[0]!.querySelector("td[colspan]");
}

function cellText(row: HTMLTableRowElement, col: number): string {
  const c = row.cells[col] as HTMLElement | undefined;
  return c ? (c.dataset.sort ?? c.innerText ?? c.textContent ?? "") : "";
}

function apply(body: HTMLTableSectionElement): void {
  const st = states.get(body);
  if (!st) return;
  const rows = [...body.rows];
  rows.forEach((r, i) => { if (!original.has(r)) original.set(r, i); });
  const vals = new Map(rows.map((r) => [r, sortValue(cellText(r, st.col))]));
  const sorted = [...rows].sort((a, b) => {
    const va = vals.get(a)!, vb = vals.get(b)!;
    if (va === null || vb === null) return compare(va, vb);  // empty last in both directions
    return compare(va, vb) * st.dir || (original.get(a)! - original.get(b)!);
  });
  if (sorted.every((r, i) => r === rows[i])) return;
  st.observer.disconnect();
  sorted.forEach((r) => body.appendChild(r));
  st.observer.observe(body, { childList: true });
}

function mark(th: HTMLTableCellElement, dir: 1 | -1 | 0): void {
  th.parentElement?.querySelectorAll("th").forEach((x) => { x.classList.remove("mf-asc", "mf-desc"); x.removeAttribute("aria-sort"); });
  if (dir) {
    th.classList.add(dir === 1 ? "mf-asc" : "mf-desc");
    th.setAttribute("aria-sort", dir === 1 ? "ascending" : "descending");
  }
}

function onClick(ev: MouseEvent): void {
  const th = (ev.target as HTMLElement | null)?.closest("th");
  if (!th || th.classList.contains("sortable") || (ev.target as HTMLElement).closest("button, input, select, a")) return;
  const table = th.closest("table");
  if (!table || !eligible(table) || !table.tHead!.contains(th)) return;
  const body = table.tBodies[0]!;
  const col = th.cellIndex;
  const cur = states.get(body);
  const next: 1 | -1 | 0 = !cur || cur.col !== col ? 1 : cur.dir === 1 ? -1 : 0;
  if (cur) cur.observer.disconnect();
  if (next === 0) {
    states.delete(body);
    [...body.rows].sort((a, b) => (original.get(a) ?? 0) - (original.get(b) ?? 0)).forEach((r) => body.appendChild(r));
    mark(th, 0);
    return;
  }
  const observer = new MutationObserver(() => apply(body));
  states.set(body, { col, dir: next, observer });
  mark(th, next);
  apply(body);
}

let installed = false;
/** Install once (the app layout does it). */
export function installTableSort(): void {
  if (installed || typeof document === "undefined") return;
  installed = true;
  document.addEventListener("click", onClick);
}
