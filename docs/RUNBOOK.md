
# House Price API: Runbook

## Service level objectives (measured over 7 days)

| SLO | Target | Where to see it |
|---|---|---|
| Availability | >= 99% of /predict requests return non-5xx | Grafana: requests by status |
| Latency | p95 /predict < 200 ms | Grafana: p95 latency; HighLatency alert |
| Monitoring freshness | monitoring_job succeeded in the last 26 h | Dagster: runs |
| Model quality | live RMSE <= 1.5 x holdout RMSE | Grafana: latest monitoring checks |

## Daily operations

- Start: `docker compose up -d`
- Stop: `docker compose down`
- Never use `docker compose down -v` unless you intend to delete persistent data.
- Status: `docker compose ps`
- Model status: `docker compose exec api mlops-model status`
- API logs: `docker compose logs -f api`
- Search structured logs using the `request_id`.

## Release a new model

1. Run `training_job` in Dagster.
2. Open MLflow and review the candidate's model card, metrics and promotion gate tags.
3. Promote the approved candidate:
   `docker compose exec api mlops-model promote`
4. Restart the API:
   `docker compose restart api`
5. Run the smoke test:
   `uv run python scripts/smoke_test.py --api-key YOUR_KEY`
6. Verify that every check displays PASS.
7. Monitor the Grafana dashboard for 15 minutes.

## Roll back a model

1. Run:
   `docker compose exec api mlops-model rollback`
2. Restart:
   `docker compose restart api`
3. Run the smoke test.
4. Check `/ready` to verify the restored model version.

## Alert -> action

| Alert / signal | First checks | Typical fix |
|---|---|---|
| ApiDown | `docker compose ps`, API logs | Restart API; check memory |
| ModelNotLoaded | API logs, MLflow health, champion alias | Fix MLflow; promote a valid model; restart API |
| HighErrorRate | API exception logs | Investigate; roll back if caused by a release |
| HighLatency | `docker stats`, training activity | Investigate slow requests and resource usage |
| PredictionLogFailures | PostgreSQL status and logs | Restart PostgreSQL; check disk space |
| input_drift failed | HTML drift report in `artifacts/reports/` | Investigate feature drift; consider retraining |
| live_rmse failed | Predictions and feedback in PostgreSQL | Investigate data shift; retrain or roll back |
| Validation failed | `artifacts/reports/validation.json` | Fix or reject invalid source data |

Never weaken validation rules simply to make a pipeline pass.

## Backups

Back up the PostgreSQL databases:

```bash
docker compose exec -T postgres pg_dumpall -U postgres > backup-$(date +%F).sql
```

Back up MLflow artifacts:

```bash
docker run --rm \
  -v modern-mlops_mlflow-artifacts:/data \
  -v "$PWD":/backup \
  alpine:3.20 \
  tar czf /backup/mlflow-artifacts-$(date +%F).tgz -C /data .
```

Restore by recreating the required volumes and loading the database dump and artifact archive.

Practice a full restoration at least once.

Note: These backup commands use Bash syntax and must be adapted for Windows CMD.

## Rotate an API key

1. Add the new key to `API_KEYS`, separated by a comma.
2. Recreate the API container to apply the updated environment.
3. Update API clients to use the new key.
4. Remove the old key from `API_KEYS`.
5. Recreate the API container again.
6. Verify authentication with the smoke test.

## Retraining policy

- Scheduled: `training_job` runs every Monday and produces a candidate.
- On signal: run training manually if drift or live accuracy fails.
- Promotion: requires human approval and a successful promotion gate.
- Rollback: restore the previous model if quality, latency or errors worsen after release.
