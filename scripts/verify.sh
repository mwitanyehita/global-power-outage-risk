#!/usr/bin/env bash
# One-command verification + regeneration of every derived artifact.
#
#   bash scripts/verify.sh
#
# Order matters: boundaries -> labels -> dataset -> experiments -> joins.
# Everything is deterministic; git-ignored outputs are rebuilt in place.
# A failing test does NOT stop the regeneration - every stage is attempted
# and the script exits non-zero at the end if anything failed.
set -uo pipefail
cd "$(dirname "$0")/.."

PY=${PY:-.venv/bin/python}
status=0

echo "== 1/3 full test suite (hermetic; skips absent-raw-data cases) =="
"$PY" -m pytest -q || status=1

echo
echo "== 2/3 regenerate derived artifacts =="
for mod in src.geo.units src.labels.eskom src.labels.collapse_days \
           src.datasets.zaf_daily src.geo.join; do
  if "$PY" -m "$mod"; then
    echo "  ok: $mod"
  else
    echo "  FAILED: $mod"; status=1
  fi
done

echo
echo "== 3/3 experiments + reports =="
for mod in src.experiments.zaf_daily_model src.experiments.zaf_ablation \
           src.experiments.nga_baseline src.eda.coverage_maps; do
  if "$PY" -m "$mod"; then
    echo "  ok: $mod"
  else
    echo "  FAILED: $mod"; status=1
  fi
done

echo
if [ "$status" -eq 0 ]; then
  echo "verify: OK - all tests green, all artifacts and reports regenerated"
else
  echo "verify: FAILED - see the failing stage above"
fi
exit "$status"
