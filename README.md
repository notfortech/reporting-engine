# StudioTech BI Report Engine

Deterministic FastAPI service for generating populated interactive analytics reports from an Analytics Blueprint JSON file and an uploaded CSV or Excel dataset.

## Run locally

```bash
python -m pip install -r requirements.txt
python -m uvicorn app:app --host 0.0.0.0 --port 8000
```

Open Swagger:

```text
http://localhost:8000/docs
```

## Health check

```bash
curl http://localhost:8000/health
```

Expected response:

```json
{"status":"healthy"}
```

## Generate a report locally

Swagger exposes `POST /generate-report` with two multipart upload fields:

- `blueprint`: Analytics Blueprint JSON, for example `RTO_Analytics_Blueprint.json`
- `dataset`: CSV or Excel dataset, for example `RTO_Mock_Data.xlsx`

Using curl:

```bash
curl -X POST "http://localhost:8000/generate-report" \
  -F "blueprint=@RTO_Analytics_Blueprint.json;type=application/json" \
  -F "dataset=@RTO_Mock_Data.xlsx;type=application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
```

The response includes a `report_id`, `report_url`, validation summary, and generated file paths.

View the generated report:

```bash
curl http://localhost:8000/report/<report_id>
```

Fetch validation JSON:

```bash
curl http://localhost:8000/validation/<report_id>
```

## Test on Azure App Service

Replace `<app-name>` with the Azure App Service name.

Swagger:

```text
https://<app-name>.azurewebsites.net/docs
```

Health:

```bash
curl https://<app-name>.azurewebsites.net/health
```

Generate a report:

```bash
curl -X POST "https://<app-name>.azurewebsites.net/generate-report" \
  -F "blueprint=@RTO_Analytics_Blueprint.json;type=application/json" \
  -F "dataset=@RTO_Mock_Data.xlsx;type=application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
```

Open the report in a browser:

```text
https://<app-name>.azurewebsites.net/report/<report_id>
```

Fetch validation:

```bash
curl https://<app-name>.azurewebsites.net/validation/<report_id>
```

## Generated output

Each call writes persistent artifacts under `/home/site/wwwroot/generated_reports/{report_id}/` on Azure, or `./generated_reports/{report_id}/` locally if the Azure path is not available:

- `report.html`
- `dax_measures.dax`
- `dashboard_blueprint.json`
- `data_profile.json`
- `measure_catalogue.json`
- `validation_report.json`

The engine is deterministic: Python calculates all values from the uploaded dataset, HTML renders calculated results, DAX files document equivalent Power BI measures, and validation JSON records deterministic PASS/FAIL checks.
