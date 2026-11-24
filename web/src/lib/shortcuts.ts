/** Every keyboard shortcut, in one list: the shortcuts sheet (``?``) shows it; the handlers live where they act
 *  (Layout for going places, the run page for its menu). A new shortcut is added here and where it acts. */
export interface Shortcut { keys: string[]; label: string; where: "everywhere" | "run page" | "lists" | "dialogs" }

export const SHORTCUTS: Shortcut[] = [
  { keys: ["⌘K", "/"], label: "Search runs, paste a run id / studio link / folder, run a command", where: "everywhere" },
  { keys: ["?"], label: "This list of shortcuts", where: "everywhere" },
  { keys: ["g", "h"], label: "Today", where: "everywhere" },
  { keys: ["g", "r"], label: "Runs", where: "everywhere" },
  { keys: ["g", "l"], label: "Local folders", where: "everywhere" },
  { keys: ["g", "c"], label: "Compare", where: "everywhere" },
  { keys: ["g", "p"], label: "Progression", where: "everywhere" },
  { keys: ["g", "t"], label: "Studies", where: "everywhere" },
  { keys: ["g", "k"], label: "Check a run", where: "everywhere" },
  { keys: ["g", "a"], label: "Ask", where: "everywhere" },
  { keys: ["g", "s"], label: "Settings", where: "everywhere" },
  { keys: ["."], label: "More actions (⋯): report, study, dossier, copy as mf, watch…", where: "run page" },
  { keys: ["↑", "↓"], label: "Move through suggestions and menus", where: "lists" },
  { keys: ["Esc"], label: "Close the palette, a menu, the Ask drawer or this sheet", where: "dialogs" },
];
