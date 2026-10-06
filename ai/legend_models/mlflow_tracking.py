"""MLflow tracking for the legend sub-sheet model fleet — LOCAL DEV ONLY.

Generalises the line_tagger pattern to N sheets:
  - one experiment per sheet:   legend/<sheet_key>
  - one registered model each:  legend_<sheet_key>   (+ @champion alias)
  - one orchestrator experiment: legend_orchestrator (manifest of the fleet)
  - optional format router model: legend_format_router

Design contract (same as line_tagger): if mlflow is missing, the tracking
server is down, or LEGEND_MODELS_MLFLOW=0, everything degrades to untracked
local training and never raises.
"""
from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from pathlib import Path

from . import config

logger = logging.getLogger('legend_models.mlflow')

_state = {'mlflow': None, 'tried': False, 'experiments': set()}


def _mlflow():
    """Import mlflow once; return None if unavailable or disabled."""
    if _state['tried']:
        return _state['mlflow']
    _state['tried'] = True
    if os.environ.get('LEGEND_MODELS_MLFLOW', '1') == '0':
        logger.info('MLflow disabled via LEGEND_MODELS_MLFLOW=0')
        return None
    try:
        import mlflow  # noqa: PLC0415 — intentional lazy import
        mlflow.set_tracking_uri(os.environ.get('MLFLOW_TRACKING_URI', 'http://localhost:5000'))
        _state['mlflow'] = mlflow
    except ImportError:
        logger.info('mlflow not installed — training untracked (pip install mlflow)')
    except Exception as e:  # noqa: BLE001 — tracking must never break training
        logger.warning('MLflow init failed (%s) — training untracked', e)
    return _state['mlflow']


@contextmanager
def start_run(sheet_key: str | None = None, run_name: str | None = None, tags: dict | None = None):
    """Start a run in the sheet's experiment (or the orchestrator experiment
    when sheet_key is None). Yields None when tracking is off."""
    mlf = _mlflow()
    if mlf is None:
        yield None
        return
    try:
        experiment = (config.ORCHESTRATOR_EXPERIMENT if sheet_key is None
                      else config.experiment_name(sheet_key))
        if experiment not in _state['experiments']:
            mlf.set_experiment(experiment)
            _state['experiments'].add(experiment)
        run = mlf.start_run(run_name=run_name, tags=tags or {})
    except Exception as e:  # noqa: BLE001
        logger.warning('MLflow run failed (%s) — continuing untracked', e)
        yield None
        return
    try:
        yield run
    finally:
        try:
            mlf.end_run()
        except Exception:  # noqa: BLE001
            pass


def log_params(params: dict) -> None:
    mlf = _mlflow()
    if mlf and mlf.active_run():
        mlf.log_params({k: str(v) for k, v in params.items()})


def log_metrics(metrics: dict, step: int | None = None) -> None:
    mlf = _mlflow()
    if mlf and mlf.active_run():
        mlf.log_metrics({k: float(v) for k, v in metrics.items()
                         if isinstance(v, (int, float))}, step=step)


def log_export_artifacts(out_dir: str | Path) -> None:
    """Log the whole export/ directory as run artifacts."""
    mlf = _mlflow()
    if mlf and mlf.active_run():
        mlf.log_artifacts(str(out_dir), artifact_path=config.EXPORT_DIRNAME)


def register_model_from_run(sheet_key: str | None = None,
                            model_name: str | None = None) -> str | None:
    """Register the active run's export/ artifacts; return run_id or None.

    Uses the two-step client API: MLflow 3's fluent register_model()
    requires a flavor-logged model, while our artifacts are plain files —
    create_registered_model + create_model_version(source=artifact_uri)
    handles those cleanly.
    """
    mlf = _mlflow()
    run = mlf.active_run() if mlf else None
    if not run:
        return None
    name = model_name or config.model_name(sheet_key)
    try:
        from mlflow.tracking import MlflowClient  # noqa: PLC0415
        client = MlflowClient()
        try:
            client.create_registered_model(name)
        except Exception:  # noqa: BLE001 — already exists
            pass
        client.create_model_version(
            name=name,
            source=f'{run.info.artifact_uri}/{config.EXPORT_DIRNAME}',
            run_id=run.info.run_id,
        )
        return run.info.run_id
    except Exception as e:  # noqa: BLE001
        logger.warning('Model registration failed (%s) — run kept as experiment only', e)
        return None


def set_champion_alias(run_id: str, sheet_key: str | None = None,
                       model_name: str | None = None) -> bool:
    """Point the champion alias of the sheet's model at this run's version."""
    mlf = _mlflow()
    if mlf is None:
        return False
    name = model_name or config.model_name(sheet_key)
    try:
        from mlflow.tracking import MlflowClient  # noqa: PLC0415
        client = MlflowClient()
        for mv in client.search_model_versions(f"name='{name}'"):
            if mv.run_id == run_id:
                client.set_registered_model_alias(name, config.CHAMPION_ALIAS, mv.version)
                return True
        logger.warning('No registered version of %s found for run %s', name, run_id)
    except Exception as e:  # noqa: BLE001
        logger.warning('Setting champion alias failed: %s', e)
    return False


def log_manifest(manifest: dict, filename: str = 'fleet_manifest.json') -> None:
    """Attach the fleet manifest (sheet → format → run/version) to a run."""
    import json
    import tempfile

    mlf = _mlflow()
    if not (mlf and mlf.active_run()):
        return
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / filename
        p.write_text(json.dumps(manifest, indent=2), encoding='utf-8')
        mlf.log_artifact(str(p))
