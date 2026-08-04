# Data Mining Platform

## Overview
AI-powered data integration and transformation platform with Wrench project integration. Provides Tableau Prep-style visual pipeline building for creating master files from multiple documents.

## Features

### 1. Wrench Integration
- **Project Selection**: Browse and select Wrench projects
- **Document Search**: Search and select multiple documents from Wrench
- **Automatic Download**: Documents are automatically retrieved for processing

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
- **Real-time Processing**: Execute pipeline and see live progress
- **Data Preview**: View first 20 rows of results
- **Master File**: Export to CSV, Excel, JSON, or Parquet
- **Statistics**: Row count, execution time, file size

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
POST   /api/v1/data-mining/projects/{id}/extract_data/      - Extract data from docs
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
- `data_mining.view` - View own projects
- `data_mining.create` - Create new projects
- `data_mining.execute` - Run pipelines
- `data_mining.admin` - View all projects

## Future Enhancements
- [ ] Real document extraction (PDF tables, Excel sheets)
- [ ] S3 integration for file storage
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
