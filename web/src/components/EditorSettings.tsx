import { Code2 } from "lucide-react";
import { useEffect, useState } from "react";
import { EDITORS, editorUrl, useEditor, type EditorId } from "../lib/editor";
import { Card } from "./ui";

/** Settings → Editor: code references (file:line from the codebase's expectations and knowledge) open in your editor. */
export function EditorSettings() {
  const [pref, setPref] = useEditor();
  const [root, setRoot] = useState(pref.root);
  useEffect(() => setRoot(pref.root), [pref.root]);
  const example = editorUrl("src/model/head.py:42", { ...pref, root });
  return (
    <Card title={<span className="row"><Code2 size={15} /> Editor</span>} sub="Code references (file:line) open in your editor">
      <div className="col" style={{ gap: 10 }} data-testid="editor-settings">
        <label className="field">Editor
          <select className="select" aria-label="Editor" value={pref.editor} onChange={(e) => setPref({ editor: e.target.value as EditorId, root })}>
            {EDITORS.map((e) => <option key={e.id} value={e.id}>{e.label}</option>)}
          </select>
        </label>
        <label className="field">The training repository on this machine (relative references are placed under it)
          <input className="input mono" aria-label="Repository folder" placeholder="/home/you/code/training-repo" value={root} onChange={(e) => setRoot(e.target.value)} onBlur={() => setPref({ ...pref, root })} />
        </label>
        <div className="faint" style={{ fontSize: 12 }}>{example ? <>e.g. <span className="mono">src/model/head.py:42</span> → <span className="mono">{example}</span></> : "References stay text until an editor (and, for relative paths, the folder) is set."}</div>
      </div>
    </Card>
  );
}
