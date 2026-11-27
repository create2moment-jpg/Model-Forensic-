# Model Forensic documentation

| You want to … | Read |
|---|---|
| try it and look at your own run (beginner) | [Getting started in 10 minutes](guide/getting-started.md) |
| answer a question with several runs (intermediate) | [Studies](guide/studies.md) |
| tune analyses, write plugins, use the notebook (expert) | [Reference](guide/reference.md), [Notebook](notebook.md), [Development](development.md) |
| run it for a team | [Shared deployment](deploy.md) |
| fix a connection problem | [Troubleshooting](troubleshooting.md), [AGENTS.md](../AGENTS.md) |
| know how far to trust a finding | [Calibration](calibration.md), [Study reproduction](study-reproduction.md), [AI eval](ai-eval.md) |
| learn more from the loss: research ideas for the tool and for the training code | [Learning from the loss](guide/learning-from-the-loss.md) |
| quick answers | [FAQ](guide/faq.md) |
| understand the product's design (the answer first, five places, the verdict) | [Product redesign](design/product-redesign.md) |
| see what is planned next (studio parity: durations, resources, pipelines, sweeps; tracks for recurring training) | [Azure studio parity and tracks](design/azure-parity-and-tracks.md) |
| refresh the screenshots in these docs | `cd web && npx vite build && MF_SCREENSHOTS=1 npx playwright test e2e/screenshots.spec.ts --trace off` |

Architecture: [architecture.md](architecture.md) and the [ADRs](adr/). Changes: [CHANGELOG](../CHANGELOG.md).
