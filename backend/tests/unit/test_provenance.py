"""Dataset provenance and compute details — the v1 tool's cases (same log/config shapes), plus per-stage pages."""

from fastapi.testclient import TestClient

from model_forensic.analysis.provenance import LogLine, compute_details, dataset_provenance, gpus_in_file
from model_forensic.api.app import create_app
from model_forensic.shared import yamlsafe

CONFIG = yamlsafe.load("""\
trainer:
  default_root_dir: !!python/object/apply:pathlib.PosixPath [/mnt/tmp]
  accelerator: gpu
  devices: 2
  precision: bf16-mixed
data_config:
  batch_size_train: 4
  num_workers_train: 8
tasks:
  det:
    data:
      batch_size_train: 2
eval_config:
  run_environment:
    num_processes: 2
loomy_data_config:
  dataset_name: release_multitask_selection
  dataset_basepath: https://example.invalid/loomy-catalogs/testing/ada/6
  dataset_version: {TRAINING: 2, VALIDATION: 1, TEST: 2, CALIBRATION: 1, QUANTIZATION_CHECK: 1}
  local_dataset_name: release_multitask
  local_dataset_basepath: /opt/test_data/gt_loomy
  local_dataset_version: {TRAINING: 1, TEST: 1}
  infer_latest_version_int: false
  use_local_dataset: false
  dataset_split: null
  predict_usage: TEST
""")

DS_LOG = [
    "2026-11-05 06:41:04 dataset_handling INFO loading dataset gt_train:13,0 from index",
    "2026-11-05 06:41:04 dataset_handling INFO loading dataset gt_train:13,1 from index",
    "2026-11-05 06:41:05 visualization INFO Loaded Loomy dataset with name=gt_test, version=14, usage=3, "
    "dataset_index_location_usage=https://example.invalid/loomy-catalogs/gt/6/test/dataset_index",
    "2026-11-05 06:41:06 dataset_handling INFO loading dataset gt_test:14,None from index",
]
GPU_INFO = (
    "NvidiaGpuInfo(name='NVIDIA H100 NVL', bus_id='00000001:00:00.0', memory_total=100485038080, "
    "vbios_version='96.00.9F.00.04', driver_version='580.95.05', max_pci_link_gen='5')"
)
PREFIX = "2026-11-05 06:41:02 xtorch_common.helpers[266][MainThread] INFO Detected GPUs: ["
GPU_LINES = [
    "INFO GPU (1/2): name = 'NVIDIA H100 NVL', total memory = 93.58 GiB, driver version = '580.95.05', "
    "VBIOS version = '96.00.9F.00.04'",
    "INFO GPU (2/2): name = 'NVIDIA H100 NVL', total memory = 93.58 GiB, driver version = '580.95.05'",
]


def lines(text: list[str], path: str = "user_logs/std_log.txt", stage: str = "") -> list[LogLine]:
    return [LogLine(stage, path, x) for x in text]


def test_dataset_requested_and_loaded() -> None:
    p = dataset_provenance([("composed_config.yaml", CONFIG)], lines(DS_LOG))
    assert p.dataset_name == "release_multitask_selection" and p.predict_usage == "TEST"
    assert {f.split: f.version for f in p.configured} == {
        "TRAINING": "2",
        "VALIDATION": "1",
        "TEST": "2",
        "CALIBRATION": "1",
        "QUANTIZATION_CHECK": "1",
    }
    assert p.infer_latest_version == "false" and not p.use_local_dataset
    loaded = {f.split: f for f in p.loaded}
    assert loaded["train"].version == "13" and loaded["test"].version == "14"
    assert loaded["test"].index_url.endswith("/test/dataset_index")  # the richer line wins
    assert any("not used" in n for n in p.notes)  # local versions configured but inactive
    assert {m.split(":")[0] for m in p.mismatches} == {"train", "test"}  # requested 2 vs loaded 13/14


def test_dataset_missing_evidence_is_explained() -> None:
    p = dataset_provenance([], [])
    assert any("Configs preset" in n for n in p.notes) and any("Logs preset" in n for n in p.notes)
    p = dataset_provenance([("eval.yaml", {"trainer": {}})], [])
    assert any("loomy_data_config" in n for n in p.notes)


def test_gpus_nvidia_info_relogged_and_two_gpus() -> None:
    assert len(gpus_in_file([PREFIX + GPU_INFO + "]", PREFIX + GPU_INFO + "]"], "f")) == 1  # per-process repeat
    two = gpus_in_file([PREFIX + GPU_INFO + ", " + GPU_INFO.replace("00000001", "00000002") + "]"], "f")
    assert len(two) == 2 and two[0].memory_gib == "93.58" and two[0].pci_link_gen == "5"


def test_gpus_k_of_n_lines_and_cpu_run() -> None:
    g = gpus_in_file(GPU_LINES, "f")
    assert len(g) == 2 and g[0].vbios_version == "96.00.9F.00.04" and g[1].vbios_version == ""
    d = compute_details([], lines(["2026-11-15 02:41:32 helpers INFO Detected GPUs: []"]))
    assert d.cpu_only and not d.gpus and "CPU" in d.notes[0]


def test_compute_parallelism_overrides_and_multinode() -> None:
    log = lines([PREFIX + GPU_INFO + "]"], "node0.txt") + lines([PREFIX + GPU_INFO + "]"], "node1.txt")
    d = compute_details([("composed_config.yaml", CONFIG)], log)
    assert d.gpu_log_files == 2 and len(d.gpus) == 2  # one row per node file
    kv = {p.key: p.value for p in d.parallelism}
    assert kv["devices"] == "2" and kv["batch_size_train"] == "4" and kv["num_processes"] == "2"
    assert any("per-task data blocks" in n for n in d.notes)


def test_run_pages_per_stage_and_redaction(container) -> None:
    c = TestClient(create_app(container))
    d = c.get("/api/v1/runs/local:xf-pipe-finetune/dataset").json()
    stages = {s["stage"]: s for s in d["stages"]}
    assert {"train_finetune", "evaluate_pretrain", "evaluate_finetune"} <= set(stages)
    assert stages["evaluate_finetune"]["mismatches"] and not stages["evaluate_pretrain"]["mismatches"]
    url = stages["train_finetune"]["loaded"][-1]["index_url"]
    assert "SECRET123" not in url and "sig=" in url
    k = c.get("/api/v1/runs/local:xf-pipe-finetune/compute").json()
    tr = next(s for s in k["stages"] if s["stage"] == "train_finetune")
    assert len(tr["gpus"]) == 8 and tr["gpus"][0]["name"] == "NVIDIA A100-SXM4-80GB"
    assert tr["platform"].get("compute_target") == "a100-cluster"
