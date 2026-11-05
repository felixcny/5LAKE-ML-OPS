"""Scheduled monitoring checks: input drift and real-world accuracy."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import numpy as np
import pandas as pd
from evidently import Report
from evidently.presets import DataDriftPreset
from mlflow.tracking import MlflowClient

from mlops import settings
from mlops.db import PredictionStore


@dataclass
class CheckResult:
    """Outcome of one monitoring check. value is None when the check was skipped."""

    name: str
    passed: bool
    value: float | None
    threshold: float
    details: dict[str, Any] = field(default_factory=dict)


def champion_info() -> tuple[str, float]:
    """Return the champion version and its holdout RMSE."""

    client = MlflowClient(tracking_uri=settings.MLFLOW_TRACKING_URI)

    champion = client.get_model_version_by_alias(
        settings.MODEL_NAME,
        settings.CHAMPION_ALIAS,
    )

    holdout_rmse = client.get_run(champion.run_id).data.metrics["rmse"]

    return str(champion.version), float(holdout_rmse)


def drifted_share(snapshot) -> float:
    """Share of columns Evidently flagged as drifted."""

    for metric in snapshot.dict()["metrics"]:
        name = str(metric.get("metric_name") or metric.get("metric_id") or "")

        kind = str((metric.get("config") or {}).get("type", ""))

        if name.startswith("DriftedColumnsCount") or kind.endswith(":DriftedColumnsCount"):
            return float(metric["value"]["share"])

    raise RuntimeError("DriftedColumnsCount not found in Evidently output; check the version.")


def check_drift(
    store: PredictionStore,
    days: int = settings.DRIFT_WINDOW_DAYS,
    min_rows: int = settings.DRIFT_MIN_ROWS,
) -> CheckResult:
    """Compare recent API inputs with training data."""

    threshold = settings.DRIFT_SHARE_THRESHOLD
    predictions = store.recent_predictions(days)

    if len(predictions) < min_rows:
        reason = f"only {len(predictions)} predictions in {days} days (need {min_rows})"

        return CheckResult(
            "input_drift",
            True,
            None,
            threshold,
            {"skipped": reason},
        )

    current = pd.DataFrame(predictions["features"].tolist())[settings.FEATURES].astype(float)

    reference = pd.read_parquet(
        settings.PROCESSED_PATH,
        columns=settings.FEATURES,
    )

    snapshot = Report([DataDriftPreset()]).run(
        reference_data=reference,
        current_data=current,
    )

    settings.REPORTS_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    report_path = settings.REPORTS_DIR / f"drift-{datetime.now(UTC):%Y%m%dT%H%M%SZ}.html"

    snapshot.save_html(str(report_path))

    share = drifted_share(snapshot)

    details = {
        "rows": len(current),
        "report": str(report_path),
    }

    return CheckResult(
        "input_drift",
        share <= threshold,
        share,
        threshold,
        details,
    )


def check_quality(
    store: PredictionStore,
    model_version: str,
    holdout_rmse: float,
    days: int = settings.QUALITY_WINDOW_DAYS,
    min_rows: int = settings.QUALITY_MIN_ROWS,
) -> CheckResult:
    """Measure real accuracy using predictions and actual sale prices."""

    threshold = holdout_rmse * settings.QUALITY_RMSE_RATIO

    labelled = store.labelled_predictions(
        days,
        model_version,
    )

    if len(labelled) < min_rows:
        reason = f"only {len(labelled)} labelled predictions in {days} days (need {min_rows})"

        return CheckResult(
            "live_rmse",
            True,
            None,
            threshold,
            {"skipped": reason},
        )

    errors = labelled["predicted_price"] - labelled["actual_price"]

    rmse = float(np.sqrt(np.mean(np.square(errors))))

    details = {
        "rows": len(labelled),
        "holdout_rmse": holdout_rmse,
    }

    return CheckResult(
        "live_rmse",
        rmse <= threshold,
        rmse,
        threshold,
        details,
    )
