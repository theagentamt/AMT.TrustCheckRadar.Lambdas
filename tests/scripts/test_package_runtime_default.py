"""The documented release wrapper must target the modern Lambda runtime."""
from pathlib import Path
import os
import subprocess
import pytest


def test_make_release_packages_preserve_per_function_defaults():
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(['make', '-n', 'package'], cwd=root, text=True,
                            capture_output=True, check=True)
    assert '--python-version' not in result.stdout
    assert '--skip-dependencies' not in result.stdout


def test_make_explicit_runtime_override_remains_explicit():
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(['make', '-n', 'package', 'PYTHON_VERSION=3.13'], cwd=root,
                            text=True, capture_output=True, check=True)
    assert '--python-version "3.13"' in result.stdout


@pytest.mark.parametrize('function,version', [
    ('message_consumer', '3.14'), ('message_evaluator', '3.14'),
    ('device_registration', '3.13'), ('conversation_analysis', '3.13'),
])
def test_actual_packager_selects_component_runtime_without_dependencies(tmp_path, function, version):
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(['bash', 'scripts/build_lambda_zip.sh', '--function', function,
                             '--skip-dependencies', '--output-dir', str(tmp_path)], cwd=root,
                            env={'PATH': os.environ['PATH']}, text=True, capture_output=True, check=True)
    assert f'Packaging {function} (Python {version}, arm64)' in result.stdout
    assert (tmp_path / (function + '.zip')).is_file()
