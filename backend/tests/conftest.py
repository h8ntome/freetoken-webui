"""Tests never use the developer's .env or persisted application data."""
import os
import tempfile
from pathlib import Path

root = Path(tempfile.mkdtemp(prefix='freetoken-tests-'))
os.environ['DATA_DIR'] = str(root / 'data')
os.environ['MODELS_DIR'] = str(root / 'models')
os.environ['AUTH_ENABLED'] = 'false'
os.environ['FREETOKEN_MODE'] = 'managed'
from app.config import Settings
Settings.model_config['env_file'] = None
