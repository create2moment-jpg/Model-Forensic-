import { ExternalLink } from "lucide-react";
import { editorUrl, useEditor } from "../lib/editor";

/** A code reference: a link that opens the file at the line in your editor (Settings → Editor), else plain text. */
export function CodeRef({ refText }: { refText: string }) {
  const [pref] = useEditor();
  const url = editorUrl(refText, pref);
  if (!url) return <span className="mono" title={refText}>{refText}</span>;
  return <a className="mono code-ref" href={url} title={`Open in your editor: ${refText}`} data-testid="code-ref">{refText}<ExternalLink size={10} style={{ marginLeft: 3 }} /></a>;
}
