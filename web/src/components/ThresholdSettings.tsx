import { useQuery } from "@tanstack/react-query";
import { Gauge } from "lucide-react";
import { Card, Loading, Pill } from "./ui";

interface Row { section: string; key: string; value: number; default: number; source: string; alarm_when: string; help: string }

/** Settings → Analysis thresholds: what the health questions, drift, internals and expectations compare against.
 *  Read-only here: change them in config/team.local.yaml → analysis_params (or from a calibration: mf calibrate). */
export function ThresholdSettings() {
  const q = useQuery({ queryKey: ["thresholds"], queryFn: async () => (await (await fetch("/api/v1/settings/thresholds")).json()) as { thresholds: Row[] } });
  const rows = q.data?.thresholds ?? [];
  const changed = rows.filter((r) => r.source === "team").length;
  return (
    <Card title={<span className="row"><Gauge size={15} /> Analysis thresholds</span>} fold="settings.thresholds" defaultFolded
      sub={`${rows.length} thresholds · ${changed} set by the team — edit config/team.local.yaml → analysis_params, or calibrate on real runs (mf calibrate)`}>
      {q.isLoading ? <Loading /> : (
        <table className="t compact" data-nosort data-testid="threshold-settings" style={{ tableLayout: "fixed", width: "100%" }}>
          <thead><tr><th style={{ width: 280 }}>threshold</th><th className="r" style={{ width: 80 }}>value</th><th className="r" style={{ width: 80 }}>default</th><th style={{ width: 90 }}>alarm when</th><th>meaning</th></tr></thead>
          <tbody>
            {rows.map((r) => (
              <tr key={`${r.section}.${r.key}`} data-threshold={`${r.section}.${r.key}`}>
                <td className="mono trunc" title={`${r.section}.${r.key}`}>{r.section}.{r.key}</td>
                <td className="r num">{r.value}{r.source === "team" && <> <Pill tone="accent">team</Pill></>}</td>
                <td className="r num faint">{r.default}</td>
                <td className="faint">{r.alarm_when}</td>
                <td className="faint trunc" title={r.help}>{r.help}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Card>
  );
}
