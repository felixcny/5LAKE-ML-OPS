"""Download the raw dataset and prepare it with PySpark."""

import hashlib
import json
import os
import sys
from pathlib import Path

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F
from pyspark.sql.types import DoubleType
from sklearn.datasets import fetch_openml

from mlops import settings
from mlops.validation import ValidationError, validate

REQUIRED_COLUMNS = [
    settings.ID_COLUMN,
    "YrSold",
    "MoSold",
    *settings.FEATURES,
    settings.TARGET,
]


def file_sha256(path: Path) -> str:
    """Compute the fingerprint of the raw dataset."""
    digest = hashlib.sha256()

    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)

    return digest.hexdigest()


def download_raw(force: bool = False) -> Path:
    """Download the Ames Housing dataset once."""

    if settings.RAW_PATH.exists() and not force:
        return settings.RAW_PATH

    dataset = fetch_openml(
        name="house_prices",
        version=1,
        as_frame=True,
        parser="auto",
    )

    frame = dataset.frame

    if frame is None or frame.empty:
        raise RuntimeError("OpenML returned an empty dataset.")

    if settings.ID_COLUMN not in frame.columns:
        frame.insert(0, settings.ID_COLUMN, range(1, len(frame) + 1))

    settings.RAW_PATH.parent.mkdir(parents=True, exist_ok=True)

    temporary = settings.RAW_PATH.with_suffix(".csv.tmp")
    frame.to_csv(temporary, index=False)
    temporary.replace(settings.RAW_PATH)

    return settings.RAW_PATH


def create_spark(app_name: str = "modern-mlops") -> SparkSession:
    """Create a local Spark session."""

    os.environ.setdefault("PYSPARK_PYTHON", sys.executable)
    os.environ.setdefault("PYSPARK_DRIVER_PYTHON", sys.executable)

    return (
        SparkSession.builder.appName(app_name)
        .master(os.getenv("SPARK_MASTER", "local[2]"))
        .config("spark.driver.memory", settings.SPARK_DRIVER_MEMORY)
        .config("spark.sql.shuffle.partitions", "4")
        .config("spark.sql.session.timeZone", "UTC")
        .config("spark.ui.enabled", "false")
        .config("spark.driver.host", "127.0.0.1")
        .config("spark.driver.bindAddress", "127.0.0.1")
        .getOrCreate()
    )


def load_raw(spark: SparkSession) -> DataFrame:
    """Load the CSV and explicitly convert numeric columns."""

    df = spark.read.option("header", True).csv(str(settings.RAW_PATH))

    for column in REQUIRED_COLUMNS:
        if column in df.columns:
            df = df.withColumn(column, F.col(column).cast(DoubleType()))

    return df


def prepare_data() -> dict:
    """Validate, clean and save the dataset as Parquet."""

    if not settings.RAW_PATH.exists():
        raise FileNotFoundError(f"Raw data not found at {settings.RAW_PATH}.")

    settings.REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    spark = create_spark()

    try:
        raw = load_raw(spark)

        # Validate BEFORE cleaning
        report = validate(raw)

        settings.VALIDATION_REPORT_PATH.write_text(json.dumps(report.to_dict(), indent=2))

        if not report.passed:
            raise ValidationError("; ".join(report.failures))

        # Build a timestamp from sale year and month
        sale_date = F.format_string(
            "%04d-%02d-01",
            F.col("YrSold").cast("int"),
            F.col("MoSold").cast("int"),
        )

        clean = (
            raw.dropna(subset=REQUIRED_COLUMNS)
            .withColumn(
                settings.ENTITY_KEY,
                F.col(settings.ID_COLUMN).cast("long"),
            )
            .withColumn(
                settings.TIMESTAMP,
                F.to_timestamp(sale_date, "yyyy-MM-dd"),
            )
            .select(
                settings.ENTITY_KEY,
                settings.TIMESTAMP,
                *settings.FEATURES,
                settings.TARGET,
            )
        )

        settings.PROCESSED_PATH.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        clean.write.mode("overwrite").parquet(str(settings.PROCESSED_PATH))

        clean_rows = spark.read.parquet(str(settings.PROCESSED_PATH)).count()

    finally:
        spark.stop()

    summary = {
        "raw_sha256": file_sha256(settings.RAW_PATH),
        "raw_rows": report.row_count,
        "clean_rows": clean_rows,
        "dropped_rows": report.row_count - clean_rows,
    }

    settings.DATA_SUMMARY_PATH.write_text(json.dumps(summary, indent=2))

    return summary


if __name__ == "__main__":
    download_raw()
    print(json.dumps(prepare_data(), indent=2))
