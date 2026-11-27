import { api } from "../api/client";

/** Plan a prefetch (review sizes and files first) and open it on the Offline copies page. Lives outside the page so
 *  the run page, the palette and the files view use it without loading the page itself. */
export async function planAndOpen(nav: (to: string) => void, body: { run: string; profile?: string; stages?: string[]; paths?: string[] }): Promise<void> {
  const t = await api.prefetchPlan(body);
  nav(`/prefetch?task=${encodeURIComponent(t.id)}`);
}
