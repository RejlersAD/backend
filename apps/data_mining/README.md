# Data Mining Platform

## Overview
Data integration and transformation tools with Wrench project selection and a visual pipeline editor. Genuine document extraction is currently unavailable. Existing saved extracted tables can be transformed and exported; the UI's create/pipeline persistence gaps (audit F02) are not corrected by F01.

## Operational contract — F01 correction, 24 September 2026

The extraction action no longer writes fixed sample rows. It returns HTTP 503
`extraction_unavailable` after authorization and record lookup, without changing
any sources, results, status or configuration. The same unavailable outcome
prevents execution from silently omitting selected sources that lack completed data.

| Operation | Required access and actual outcome |
| --- | --- |
| `POST projects/{id}/extract_data/` | Existing guarded `data_mining.create` plus project scope; HTTP 503 without fabricated results or database mutations. |
| `POST projects/{id}/execute_pipeline/` | Existing create guard **and** current export grant, project scope, saved pipeline and prepared data for every source. Serializes the existing transformation result, saves a unique private object, checks existence and exact read-back, then commits result metadata. |
| `GET projects/{id}/download_master/` | Existing export guard plus current project scope. Streams an actual stored file as an attachment; never redirects to an arbitrary stored URL. Optional `expected_master_file` must match the current artifact, otherwise HTTP 409 `artifact_changed`. |

Execution success returns `{status: 'completed', artifact_available: true,
master_file, filename, rows_processed, execution_time, preview}`. `master_file` is
an opaque project-owned storage key, **not a public download URL**. The frontend
requires the explicit completion/artifact confirmation and uses its authenticated
API client for downloading, including the expected key.

Errors retain the existing string `error` convention plus stable `code`:

- HTTP 400 `pipeline_missing`: no saved pipeline; F02 remains unresolved.
- HTTP 503 `extraction_unavailable`: source extraction is unsupported or some
  selected source lacks prepared data. No invented rows or partial completion.
- HTTP 503 `artifact_storage_unavailable`: save, existence, read-back or open failed.
- HTTP 500 `pipeline_execution_failed`: transformation, serialization or metadata
  persistence failed. No new completed result or download is claimed.
- HTTP 404 `artifact_unavailable`: missing file or unsupported legacy/arbitrary
  storage pointer. Existing values are preserved, not repaired or deleted.
- HTTP 409 `artifact_changed`: another execution replaced the currently displayed
  result; the requested download produces no bytes.
- Existing authentication, action denials (403) and safe cross-owner lookup (404)
  remain authoritative, including when grants change after execution.

Local artifacts use `BASE_DIR/private/data-mining`, outside the publicly served
media tree. An overlapping local root or a remote backend without declared private
ACL/authenticated URLs fails closed. Existing private remote storage is reused;
no public fallback, bucket permission or infrastructure change is introduced.
Stored keys are unique under `data-mining/{project UUID}/exports/{run UUID}.{ext}`.
Download responses are `private, no-store` and `nosniff`.

Prior files remain untouched. Previous project/step result metadata is appended
to the pipeline's existing log before latest-result fields change; log text is
preserved. Pipeline reads follow the existing project owner/admin rule. Generated
project result fields and pipeline log/time are read-only to generic edits. This
is not a new approval system or verification of historical extraction accuracy.

Failed execution rolls back database changes and preserves prior evidence. Storage
and database commits are not one distributed transaction: a failed read-back or
metadata commit may leave a new unreferenced private object, never reported as
completed. No historical cleanup runs. Live S3, Wrench and provider behavior are
not certified by local tests. Parquet requires an available pandas writer; missing
dependencies fail explicitly. CSV preserves raw values; consumers determine how
those values are interpreted. XLSX stores source text literally, including `=`
prefixes, rather than silently converting it into formulas.

The UI retains project/source selections and local pipeline steps on failure,
shows persistent unavailable/denied/failed alerts, and rejects legacy success
responses without verified-artifact fields. These input-preservation checks do
not imply that local pipeline edits are persisted (F02).

See the [scoped brief and verification evidence](../../../docs/features/data-mining-truthful-results.md)
and [guarded synthetic tests](tests.py). Shared context lives outside this Git repository.

## Features

### 1. Wrench Integration
- **Project Selection**: Browse and select Wrench projects
- **Document Search**: Search and select multiple documents from Wrench
- **Document retrieval/extraction**: No genuine extraction adapter is connected to the operational extraction action; selected metadata is retained for future authorized processing.

### 2. Visual Pipeline Builder
Soft-coded transformation operations:

#### Join Operation
Merge two datasets by matching keys
- **Types**: Inner, Left, Right, Outer Join
- **Config**: `join_type`, `left_key`, `right_key`, `right_input`

#### Filter Operation
Remove rows based on conditions
- **Operators**: equals, not_equals, greater_than, less_than, contains, is_null, etc.
- **Logic**: AND/OR combinations
- **Config**: `conditions[]`, `logic`

#### Aggregate Operation
Group and summarize data
- **Functions**: sum, avg, count, min, max, median, std, var
- **Config**: `group_by[]`, `aggregations[]`

#### Clean Operation
Data quality improvements
- **Features**: Remove duplicates, drop null rows, fill null values
- **Config**: `remove_duplicates`, `drop_null_rows[]`, `fill_null_value{}`

#### Derive Operation
Create calculated columns
- **Features**: Mathematical expressions, data type conversion
- **Config**: `new_columns[]` with `name`, `expression`, `data_type`

#### Pivot/Unpivot
Reshape data structure
- **Pivot**: Convert rows to columns
- **Unpivot**: Convert columns to rows

#### Union Operation
Stack datasets vertically
- **Features**: Column alignment, multiple inputs
- **Config**: `inputs[]`, `align_columns`

#### Rename Operation
Rename columns
- **Config**: `column_mapping{}`

#### Select Operation
Choose specific columns
- **Config**: `columns[]`

#### Sort Operation
Order data by columns
- **Config**: `sort_by[]` with `column`, `ascending`

#### Sample Operation
Take random subset
- **Config**: `sample_size` or `sample_fraction`, `random_state`

### 3. Execution & Output
- **Processing**: Synchronous execution of the existing saved pipeline using already prepared source tables
- **Data Preview**: View first 20 rows of results
- **Master File**: CSV, Excel, JSON, or Parquet only after successful serialization and verified private storage; required format dependencies must be available
- **Statistics**: Actual row count and elapsed execution time for the completed operation

## Architecture

### Backend
```
apps/data_mining/
├── models.py              - DataMiningProject, Document, Pipeline, Step
├── views.py               - RESTful API endpoints
├── serializers.py         - DRF serializers
├── transformation_engine.py - Soft-coded transformation logic
├── urls.py                - URL routing
└── migrations/            - Database migrations
```

### Frontend
```
frontend/src/pages/
└── DataMiningPlatform.jsx - Main UI component
```

### API Endpoints
```
GET    /api/v1/data-mining/projects/                  - List projects
POST   /api/v1/data-mining/projects/                  - Create project
GET    /api/v1/data-mining/projects/{id}/             - Get project
PATCH  /api/v1/data-mining/projects/{id}/             - Update project
DELETE /api/v1/data-mining/projects/{id}/             - Delete project

POST   /api/v1/data-mining/projects/{id}/add_documents/     - Add Wrench documents
POST   /api/v1/data-mining/projects/{id}/extract_data/      - Explicit extraction-unavailable outcome (503)
POST   /api/v1/data-mining/projects/{id}/execute_pipeline/  - Run pipeline
GET    /api/v1/data-mining/projects/{id}/download_master/   - Download master file

GET    /api/v1/data-mining/wrench/projects/          - List Wrench projects
GET    /api/v1/data-mining/wrench/search/            - Search Wrench documents
```

## Database Schema

### DataMiningProject
- `id` (UUID)
- `name`, `description`
- `wrench_project_number`, `wrench_project_name`
- `status` (draft, configuring, executing, completed, failed)
- `master_file_path`, `master_file_format`
- `total_documents`, `total_rows_processed`, `execution_time_seconds`
- `created_by` (ForeignKey to User)

### DataMiningDocument
- `id` (UUID)
- `project` (ForeignKey)
- `wrench_doc_number`, `wrench_doc_title`, `wrench_doc_revision`
- `file_path`, `file_type`, `file_size_bytes`
- `extraction_status`, `extracted_data` (JSON)
- `row_count`, `column_count`, `sequence_order`

### TransformationPipeline
- `id` (UUID)
- `project` (OneToOne)
- `name`, `description`
- `canvas_config` (JSON) - Visual layout
- `last_executed_at`, `execution_log`

### TransformationStep
- `id` (UUID)
- `pipeline` (ForeignKey)
- `step_name`, `operation_type`
- `config` (JSON) - Soft-coded operation config
- `input_source`, `output_preview` (JSON)
- `output_row_count`, `output_column_count`
- `sequence_order`, `status`, `error_message`
- `execution_time_ms`

## Usage Example

This is the intended journey, not a claim that new-project creation and local
pipeline configuration are fully connected. Audit F02 remains open. A newly
selected source cannot be genuinely extracted by the current route; successful
export requires an existing saved pipeline and prepared source tables.

1. **Create Project**
   ```
   Navigate to "2.4 Data Mining" in sidebar
   Click "New Project"
   Select Wrench project
   ```

2. **Add Documents**
   ```
   Search Wrench documents
   Select multiple documents (checkboxes)
   Click "Add Documents to Project"
   ```

3. **Build Pipeline**
   ```
   Add transformation steps:
   1. Clean - Remove duplicates
   2. Filter - Keep only active items
   3. Join - Merge with equipment list
   4. Aggregate - Sum by category
   5. Select - Choose final columns
   ```

4. **Execute**
   ```
   Click "Execute Pipeline"
   View results preview
   Download master file
   ```

## Soft-Coding Principles

### Adding New Transformation Operations
1. Add operation to `TRANSFORMATION_OPERATIONS` in `models.py`
2. Implement method in `TransformationEngine` class
3. Register in `operation_registry`
4. Add icon and template to frontend `TRANSFORMATION_OPERATIONS`

### Configuration Structure
All transformation configs follow JSON schema:
```json
{
  "operation_type": "filter",
  "config": {
    "conditions": [
      {"column": "status", "operator": "equals", "value": "Active"}
    ],
    "logic": "and"
  }
}
```

## RBAC Integration
Module code: `data_mining`

Permissions:
- `data_mining.read` - Read permitted projects and pipelines
- `data_mining.create` - Create/add sources and invoke the extraction/execution actions
- `data_mining.update` - Edit permitted configuration, excluding generated result/history fields
- `data_mining.export` - Required in addition to create for execution, and rechecked for download

The registered guard uses these existing action names, not a separate `execute`
grant. Project/pipeline scope retains the existing owner rule and `user.is_admin`
exception; it does not establish new organizational visibility policy.

## Future Enhancements
- [ ] Real document extraction (PDF tables, Excel sheets)
- [ ] Live validation of configured private remote storage and provider contracts
- [ ] Advanced AI transformations (NLP, classification)
- [ ] Scheduled pipeline execution
- [ ] Version control for pipelines
- [ ] Collaboration features
- [ ] Data quality metrics
- [ ] Visual DAG editor with drag-drop

## Technical Dependencies
- **pandas**: Data transformation
- **numpy**: Numerical operations
- Django + DRF: Backend framework
- React: Frontend UI
- Wrench API: Document integration

## Navigation
Located in: **2. COMMON → 2.4 Data Mining**

## Module Code
`data_mining` - Use this in RBAC configuration
