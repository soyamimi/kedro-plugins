import logging
import sys
from pathlib import Path
from unittest.mock import MagicMock

import requests
import yaml
from kedro import __version__ as kedro_version
from kedro.framework.project import pipelines
from kedro.framework.startup import ProjectMetadata
from kedro.io import DataCatalog, MemoryDataset
from kedro.pipeline import Pipeline, node
from kedro.pipeline import pipeline as modular_pipeline
from pytest import fixture, mark

from kedro_telemetry import __version__ as TELEMETRY_VERSION
from kedro_telemetry.plugin import (
    _SKIP_TELEMETRY_ENV_VAR_KEYS,
    KNOWN_CI_ENV_VAR_KEYS,
    MISSING_USER_IDENTITY,
    KedroTelemetryHook,
    _check_for_telemetry_consent,
    _format_project_statistics_data,
    _is_known_ci_env,
)

REPO_NAME = "dummy_project"
PACKAGE_NAME = "dummy_package"

MOCK_PYPROJECT_TOOLS = """
[build-system]
requires = [ "setuptools",]
build-backend = "setuptools.build_meta"

[project]
name = "spaceflights"
readme = "README.md"
dynamic = [ "dependencies", "version",]

[project.scripts]
new-proj = "spaceflights.__main__:main"

[tool.kedro]
package_name = "spaceflights"
project_name = "spaceflights"
kedro_init_version = "0.18.14"
tools = ["Linting", "Testing", "Custom Logging", "Documentation", "Data Structure", "PySpark"]
example_pipeline = "True"

[project.entry-points."kedro.hooks"]

[tool.setuptools.dynamic.dependencies]
file = "requirements.txt"

[tool.setuptools.dynamic.version]
attr = "spaceflights.__version__"

[tool.setuptools.packages.find]
where = [ "src",]
namespaces = false
"""


@fixture
def fake_metadata(tmp_path):
    metadata = ProjectMetadata(
        config_file=tmp_path / REPO_NAME / "pyproject.toml",
        package_name=PACKAGE_NAME,
        project_name="CLI Testing Project",
        project_path=tmp_path / REPO_NAME,
        source_dir=tmp_path / REPO_NAME / "src",
        kedro_init_version=kedro_version,
        tools=[],
        example_pipeline="No",
    )
    return metadata


@fixture
def fake_catalog():
    catalog = DataCatalog(
        {
            "dummy_1": MemoryDataset(),
            "dummy_2": MemoryDataset(),
            "dummy_3": MemoryDataset(),
            "parameters": MemoryDataset(),
            "params:dummy": MemoryDataset(),
        }
    )
    return catalog


def identity(arg):
    return arg


@fixture
def fake_context():
    class MockKedroContext:
        # A dummy stand-in for KedroContext sufficient for this test
        project_path = Path("")

    return MockKedroContext()


@fixture
def fake_default_pipeline():
    mock_default_pipeline = modular_pipeline(
        [
            node(identity, ["input"], ["intermediate"], name="node0"),
            node(identity, ["intermediate"], ["output"], name="node1"),
        ],
    )
    return mock_default_pipeline


@fixture
def fake_sub_pipeline():
    mock_sub_pipeline = modular_pipeline(
        [
            node(identity, ["input"], ["intermediate"], name="node0"),
        ],
    )
    return mock_sub_pipeline


@fixture
def pipeline_fixture() -> Pipeline:
    mock_pipeline = MagicMock(spec=Pipeline)
    mock_pipeline.nodes = ["node1", "node2"]
    return mock_pipeline


@fixture
def project_pipelines() -> dict[str, Pipeline]:
    return {
        "pipeline1": MagicMock(spec=Pipeline),
        "pipeline2": MagicMock(spec=Pipeline),
    }


class TestKedroTelemetryHook:
    def test_before_command_run(self, mocker, fake_metadata, caplog):
        mocker.patch(
            "kedro_telemetry.plugin._check_for_telemetry_consent", return_value=True
        )
        mocker.patch("kedro_telemetry.plugin._is_known_ci_env", return_value=True)
        mocked_anon_id = mocker.patch("kedro_telemetry.plugin._hash")
        mocked_anon_id.return_value = "digested"
        mocker.patch("kedro_telemetry.plugin.PACKAGE_NAME", "spaceflights")
        mocker.patch(
            "kedro_telemetry.plugin._get_or_create_uuid",
            return_value="user_uuid",
        )
        mocker.patch(
            "kedro_telemetry.plugin._get_or_create_project_id",
            return_value="project_id",
        )

        mocked_heap_call = mocker.patch("kedro_telemetry.plugin._send_heap_event")

        with caplog.at_level(logging.INFO):
            telemetry_hook = KedroTelemetryHook()
            command_args = ["--version"]
            telemetry_hook.before_command_run(fake_metadata, command_args)
            telemetry_hook.after_command_run()
        expected_properties = {
            "username": "user_uuid",
            "project_id": "digested",
            "project_version": kedro_version,
            "telemetry_version": TELEMETRY_VERSION,
            "python_version": sys.version,
            "os": sys.platform,
            "is_ci_env": True,
            "command": "kedro --version",
        }
        generic_properties = {
            **expected_properties,
            "main_command": "--version",
        }

        expected_calls = [
            mocker.call(
                event_name="CLI command",
                identity="user_uuid",
                properties=generic_properties,
            ),
        ]
        assert mocked_heap_call.call_args_list == expected_calls
        assert any(
            "Kedro is sending anonymous usage data with the sole purpose of improving the product. "
            "No personal data or IP addresses are stored on our side. "
            "If you want to opt out, set the `KEDRO_DISABLE_TELEMETRY` or `DO_NOT_TRACK` environment variables, "
            "or create a `.telemetry` file in the current working directory with the contents `consent: false`. "
            "Read more at https://docs.kedro.org/en/stable/configuration/telemetry.html"
            in record.message
            for record in caplog.records
        )

    def test_before_command_run_with_tools(self, mocker, fake_metadata):
        mocker.patch(
            "kedro_telemetry.plugin._check_for_telemetry_consent", return_value=True
        )
        mocker.patch("kedro_telemetry.plugin._is_known_ci_env", return_value=True)
        mocked_anon_id = mocker.patch("kedro_telemetry.plugin._hash")
        mocked_anon_id.return_value = "digested"
        mocker.patch("kedro_telemetry.plugin.PACKAGE_NAME", "spaceflights")
        mocker.patch(
            "kedro_telemetry.plugin._get_or_create_uuid",
            return_value="user_uuid",
        )
        mocker.patch(
            "kedro_telemetry.plugin._get_or_create_project_id",
            return_value="project_id",
        )

        mocked_heap_call = mocker.patch("kedro_telemetry.plugin._send_heap_event")
        mocker.patch("builtins.open", mocker.mock_open(read_data=MOCK_PYPROJECT_TOOLS))
        mocker.patch("pathlib.Path.exists", return_value=True)
        telemetry_hook = KedroTelemetryHook()
        command_args = ["--version"]
        telemetry_hook.before_command_run(fake_metadata, command_args)
        telemetry_hook.after_command_run()
        expected_properties = {
            "username": "user_uuid",
            "project_id": "digested",
            "project_version": kedro_version,
            "telemetry_version": TELEMETRY_VERSION,
            "python_version": sys.version,
            "os": sys.platform,
            "is_ci_env": True,
            "command": "kedro --version",
            "tools": "Linting, Testing, Custom Logging, Documentation, Data Structure, PySpark",
            "example_pipeline": "True",
        }
        generic_properties = {
            **expected_properties,
            "main_command": "--version",
        }

        expected_calls = [
            mocker.call(
                event_name="CLI command",
                identity="user_uuid",
                properties=generic_properties,
            ),
        ]
        assert mocked_heap_call.call_args_list == expected_calls

    def test_before_command_run_empty_args(self, mocker, fake_metadata):
        mocker.patch(
            "kedro_telemetry.plugin._check_for_telemetry_consent", return_value=True
        )
        mocker.patch("kedro_telemetry.plugin._is_known_ci_env", return_value=True)
        mocked_anon_id = mocker.patch("kedro_telemetry.plugin._hash")
        mocked_anon_id.return_value = "digested"
        mocker.patch("kedro_telemetry.plugin.PACKAGE_NAME", "spaceflights")
        mocker.patch(
            "kedro_telemetry.plugin._get_or_create_uuid",
            return_value="user_uuid",
        )
        mocker.patch(
            "kedro_telemetry.plugin._get_or_create_project_id",
            return_value="project_id",
        )

        mocked_heap_call = mocker.patch("kedro_telemetry.plugin._send_heap_event")
        telemetry_hook = KedroTelemetryHook()
        command_args = []
        telemetry_hook.before_command_run(fake_metadata, command_args)
        telemetry_hook.after_command_run()
        expected_properties = {
            "username": "user_uuid",
            "project_id": "digested",
            "project_version": kedro_version,
            "telemetry_version": TELEMETRY_VERSION,
            "python_version": sys.version,
            "os": sys.platform,
            "is_ci_env": True,
            "command": "kedro",
        }
        generic_properties = {
            "main_command": "kedro",
            **expected_properties,
        }

        expected_calls = [
            mocker.call(
                event_name="CLI command",
                identity="user_uuid",
                properties=generic_properties,
            ),
        ]

        assert mocked_heap_call.call_args_list == expected_calls

    def test_before_command_run_no_consent_given(self, mocker, fake_metadata, caplog):
        mocker.patch(
            "kedro_telemetry.plugin._check_for_telemetry_consent", return_value=False
        )

        mocked_heap_call = mocker.patch("kedro_telemetry.plugin._send_heap_event")
        with caplog.at_level(logging.INFO):
            telemetry_hook = KedroTelemetryHook()
            command_args = ["--version"]
            telemetry_hook.before_command_run(fake_metadata, command_args)

        mocked_heap_call.assert_not_called()
        assert not any(
            "Kedro is sending anonymous usage data with the sole purpose of improving the product. "
            "No personal data or IP addresses are stored on our side. "
            "If you want to opt out, set the `KEDRO_DISABLE_TELEMETRY` or `DO_NOT_TRACK` environment variables, "
            "or create a `.telemetry` file in the current working directory with the contents `consent: false`. "
            "Read more at https://docs.kedro.org/en/latest/configuration/telemetry.html"
            in record.message
            for record in caplog.records
        )

    def test_before_command_run_connection_error(self, mocker, fake_metadata, caplog):
        mocker.patch(
            "kedro_telemetry.plugin._check_for_telemetry_consent", return_value=True
        )
        telemetry_hook = KedroTelemetryHook()
        command_args = ["--version"]

        mocked_post_request = mocker.patch(
            "requests.post", side_effect=requests.exceptions.ConnectionError()
        )
        telemetry_hook.before_command_run(fake_metadata, command_args)
        telemetry_hook.after_command_run()
        msg = "Failed to send data to Heap. Exception of type 'ConnectionError' was raised."
        assert msg in caplog.messages[-1]
        mocked_post_request.assert_called()

    def test_before_command_run_anonymous(self, mocker, fake_metadata):
        mocker.patch(
            "kedro_telemetry.plugin._check_for_telemetry_consent", return_value=True
        )
        mocker.patch("kedro_telemetry.plugin._is_known_ci_env", return_value=True)
        mocked_anon_id = mocker.patch("kedro_telemetry.plugin._hash")
        mocked_anon_id.return_value = "digested"
        mocker.patch("kedro_telemetry.plugin.PACKAGE_NAME", "spaceflights")
        mocker.patch("builtins.open", side_effect=OSError)

        mocked_heap_call = mocker.patch("kedro_telemetry.plugin._send_heap_event")
        telemetry_hook = KedroTelemetryHook()
        command_args = ["--version"]
        telemetry_hook.before_command_run(fake_metadata, command_args)
        telemetry_hook.after_command_run()
        expected_properties = {
            "username": "",
            "command": "kedro --version",
            "project_id": None,
            "project_version": kedro_version,
            "telemetry_version": TELEMETRY_VERSION,
            "python_version": sys.version,
            "os": sys.platform,
            "is_ci_env": True,
        }
        generic_properties = {
            "main_command": "--version",
            **expected_properties,
        }

        expected_calls = [
            mocker.call(
                event_name="CLI command",
                identity=MISSING_USER_IDENTITY,
                properties=generic_properties,
            ),
        ]
        assert mocked_heap_call.call_args_list == expected_calls

    def test_before_command_run_heap_call_error(self, mocker, fake_metadata, caplog):
        mocker.patch(
            "kedro_telemetry.plugin._check_for_telemetry_consent", return_value=True
        )
        mocked_heap_call = mocker.patch(
            "kedro_telemetry.plugin._send_heap_event", side_effect=Exception
        )
        telemetry_hook = KedroTelemetryHook()
        command_args = ["--version"]

        telemetry_hook.before_command_run(fake_metadata, command_args)
        telemetry_hook.after_command_run()
        msg = (
            "Something went wrong in hook implementation to send command run data to"
            " Heap. Exception:"
        )
        assert msg in caplog.messages[-1]
        mocked_heap_call.assert_called()

    def test_check_for_telemetry_consent_given(self, mocker, fake_metadata):
        Path(fake_metadata.project_path, "conf").mkdir(parents=True)
        telemetry_file_path = fake_metadata.project_path / ".telemetry"
        with open(telemetry_file_path, "w", encoding="utf-8") as telemetry_file:
            yaml.dump({"consent": True}, telemetry_file)

        assert _check_for_telemetry_consent(fake_metadata.project_path)

    def test_check_for_telemetry_consent_not_given(self, mocker, fake_metadata):
        Path(fake_metadata.project_path, "conf").mkdir(parents=True)
        telemetry_file_path = fake_metadata.project_path / ".telemetry"
        with open(telemetry_file_path, "w", encoding="utf-8") as telemetry_file:
            yaml.dump({"consent": False}, telemetry_file)

        assert not _check_for_telemetry_consent(fake_metadata.project_path)

    @mark.parametrize("env_var", _SKIP_TELEMETRY_ENV_VAR_KEYS)
    def test_check_for_telemetry_consent_skip_telemetry_with_env_var(
        self, monkeypatch, fake_metadata, env_var
    ):
        monkeypatch.setenv(env_var, "True")
        Path(fake_metadata.project_path, "conf").mkdir(parents=True)
        telemetry_file_path = fake_metadata.project_path / ".telemetry"
        with open(telemetry_file_path, "w", encoding="utf-8") as telemetry_file:
            yaml.dump({"consent": True}, telemetry_file)

        assert not _check_for_telemetry_consent(fake_metadata.project_path)

    def test_check_for_telemetry_consent_empty_file(self, mocker, fake_metadata):
        Path(fake_metadata.project_path, "conf").mkdir(parents=True)
        telemetry_file_path = fake_metadata.project_path / ".telemetry"

        with open(telemetry_file_path, "w", encoding="utf-8") as telemetry_file:
            yaml.dump({}, telemetry_file)

        assert _check_for_telemetry_consent(fake_metadata.project_path)

    def test_check_for_telemetry_consent_file_no_consent_field(
        self, mocker, fake_metadata
    ):
        Path(fake_metadata.project_path, "conf").mkdir(parents=True)
        telemetry_file_path = fake_metadata.project_path / ".telemetry"
        with open(telemetry_file_path, "w", encoding="utf8") as telemetry_file:
            yaml.dump({"nonsense": "bla"}, telemetry_file)

        assert _check_for_telemetry_consent(fake_metadata.project_path)

    def test_check_for_telemetry_consent_file_invalid_yaml(self, mocker, fake_metadata):
        Path(fake_metadata.project_path, "conf").mkdir(parents=True)
        telemetry_file_path = fake_metadata.project_path / ".telemetry"
        telemetry_file_path.write_text("invalid_ yaml")

        assert _check_for_telemetry_consent(fake_metadata.project_path)

    @mark.parametrize(
        "env_vars,result",
        [
            ({"CI": "true"}, True),
            ({"CI": "false"}, False),
            ({"CI": "false", "CODEBUILD_BUILD_ID": "Testing known CI env var"}, True),
            ({"JENKINS_URL": "Testing known CI env var"}, True),
            ({"CI": "false", "TRAVIS": "Testing known CI env var"}, True),
            ({"GITLAB_CI": "Testing known CI env var"}, True),
            ({"CI": "false", "CIRCLECI": "Testing known CI env var"}, True),
            (
                {"CI": "false", "BITBUCKET_BUILD_NUMBER": "Testing known CI env var"},
                True,
            ),
        ],
    )
    def test_check_is_known_ci_env(self, monkeypatch, env_vars, result):
        for env_var, env_var_value in env_vars.items():
            monkeypatch.setenv(env_var, env_var_value)

        known_ci_vars = KNOWN_CI_ENV_VAR_KEYS
        # Because our CI runs on Github Actions, this would always return True otherwise
        known_ci_vars.discard("GITHUB_ACTION")
        assert _is_known_ci_env(known_ci_vars) == result

    def test_after_context_created_without_kedro_run(  # noqa: PLR0913
        self,
        mocker,
        fake_catalog,
        fake_default_pipeline,
        fake_sub_pipeline,
        fake_context,
    ):
        mocker.patch.dict(
            pipelines, {"__default__": fake_default_pipeline, "sub": fake_sub_pipeline}
        )
        mocker.patch(
            "kedro_telemetry.plugin._check_for_telemetry_consent", return_value=True
        )
        mocker.patch("kedro_telemetry.plugin._is_known_ci_env", return_value=True)
        mocker.patch("kedro_telemetry.plugin._hash", return_value="digested")
        mocker.patch("kedro_telemetry.plugin.PACKAGE_NAME", "spaceflights")
        mocker.patch(
            "kedro_telemetry.plugin._get_or_create_uuid",
            return_value="user_uuid",
        )
        mocker.patch(
            "kedro_telemetry.plugin._get_or_create_project_id",
            return_value="project_id",
        )

        mocked_heap_call = mocker.patch("kedro_telemetry.plugin._send_heap_event")
        mocker.patch("kedro_telemetry.plugin.open")
        mocker.patch("kedro_telemetry.plugin.toml.load")
        mocker.patch("kedro_telemetry.plugin.toml.dump")

        # Without CLI invoked - i.e. `session.run` in Jupyter/IPython
        telemetry_hook = KedroTelemetryHook()
        telemetry_hook.after_context_created(fake_context)
        telemetry_hook.after_catalog_created(fake_catalog)

        project_properties = {
            "username": "user_uuid",
            "project_id": "digested",
            "project_version": kedro_version,
            "telemetry_version": TELEMETRY_VERSION,
            "python_version": sys.version,
            "os": sys.platform,
            "is_ci_env": True,
        }
        project_statistics = {
            "number_of_datasets": 3,
            "number_of_nodes": 2,
            "number_of_pipelines": 2,
        }
        expected_properties = {**project_properties, **project_statistics}
        expected_call = mocker.call(
            event_name="Kedro Project Statistics",
            identity="user_uuid",
            properties=expected_properties,
        )

        # The 1st call is the Project Hook without CLI
        assert mocked_heap_call.call_args_list[0] == expected_call

    def test_after_context_created_with_kedro_run(  # noqa: PLR0913
        self,
        mocker,
        fake_catalog,
        fake_metadata,
        fake_default_pipeline,
        fake_sub_pipeline,
        fake_context,
    ):
        mocker.patch.dict(
            pipelines, {"__default__": fake_default_pipeline, "sub": fake_sub_pipeline}
        )
        mocker.patch(
            "kedro_telemetry.plugin._check_for_telemetry_consent", return_value=True
        )
        mocker.patch("kedro_telemetry.plugin._is_known_ci_env", return_value=True)
        mocker.patch("kedro_telemetry.plugin._hash", return_value="digested")
        mocker.patch("kedro_telemetry.plugin.PACKAGE_NAME", "spaceflights")
        mocker.patch(
            "kedro_telemetry.plugin._get_or_create_uuid",
            return_value="user_uuid",
        )
        mocker.patch(
            "kedro_telemetry.plugin._get_or_create_project_id",
            return_value="project_id",
        )
        mocked_heap_call = mocker.patch("kedro_telemetry.plugin._send_heap_event")
        mocker.patch("kedro_telemetry.plugin.toml.load")
        mocker.patch("kedro_telemetry.plugin.toml.dump")
        # CLI run first
        telemetry_cli_hook = KedroTelemetryHook()
        command_args = ["--version"]
        telemetry_cli_hook.before_command_run(fake_metadata, command_args)

        # Follow by project run
        telemetry_hook = KedroTelemetryHook()
        telemetry_hook.after_context_created(fake_context)
        telemetry_hook.after_catalog_created(fake_catalog)

        project_properties = {
            "username": "user_uuid",
            "project_id": "digested",
            "project_version": kedro_version,
            "telemetry_version": TELEMETRY_VERSION,
            "python_version": sys.version,
            "os": sys.platform,
            "is_ci_env": True,
        }
        project_statistics = {
            "number_of_datasets": 3,
            "number_of_nodes": 2,
            "number_of_pipelines": 2,
        }
        expected_properties = {**project_properties, **project_statistics}

        expected_call = mocker.call(
            event_name="Kedro Project Statistics",
            identity="user_uuid",
            properties=expected_properties,
        )

        assert mocked_heap_call.call_args_list[0] == expected_call

    def test_after_context_created_with_kedro_run_and_tools(  # noqa: PLR0913
        self,
        mocker,
        fake_catalog,
        fake_metadata,
        fake_default_pipeline,
        fake_sub_pipeline,
        fake_context,
    ):
        mocker.patch.dict(
            pipelines, {"__default__": fake_default_pipeline, "sub": fake_sub_pipeline}
        )
        mocker.patch(
            "kedro_telemetry.plugin._check_for_telemetry_consent", return_value=True
        )
        mocker.patch("kedro_telemetry.plugin._is_known_ci_env", return_value=True)
        mocker.patch("kedro_telemetry.plugin._hash", return_value="digested")
        mocker.patch("kedro_telemetry.plugin.PACKAGE_NAME", "spaceflights")
        mocker.patch(
            "kedro_telemetry.plugin._get_or_create_uuid",
            return_value="user_uuid",
        )
        mocker.patch(
            "kedro_telemetry.plugin._get_or_create_project_id",
            return_value="project_id",
        )
        mocked_heap_call = mocker.patch("kedro_telemetry.plugin._send_heap_event")
        mocker.patch("builtins.open", mocker.mock_open(read_data=MOCK_PYPROJECT_TOOLS))
        mocker.patch("pathlib.Path.exists", return_value=True)

        # CLI run first
        telemetry_cli_hook = KedroTelemetryHook()
        command_args = ["--version"]
        telemetry_cli_hook.before_command_run(fake_metadata, command_args)

        # Follow by project run
        telemetry_hook = KedroTelemetryHook()
        telemetry_hook.after_context_created(fake_context)
        telemetry_hook.after_catalog_created(fake_catalog)

        project_properties = {
            "username": "user_uuid",
            "project_id": "digested",
            "project_version": kedro_version,
            "telemetry_version": TELEMETRY_VERSION,
            "python_version": sys.version,
            "os": sys.platform,
            "is_ci_env": True,
            "tools": "Linting, Testing, Custom Logging, Documentation, Data Structure, PySpark",
            "example_pipeline": "True",
        }
        project_statistics = {
            "number_of_datasets": 3,
            "number_of_nodes": 2,
            "number_of_pipelines": 2,
        }
        expected_properties = {**project_properties, **project_statistics}

        expected_call = mocker.call(
            event_name="Kedro Project Statistics",
            identity="user_uuid",
            properties=expected_properties,
        )

        assert mocked_heap_call.call_args_list[0] == expected_call

    def test_after_context_created_no_consent_given(self, mocker):
        fake_context = mocker.Mock()
        mocker.patch(
            "kedro_telemetry.plugin._check_for_telemetry_consent", return_value=False
        )

        mocked_heap_call = mocker.patch("kedro_telemetry.plugin._send_heap_event")
        telemetry_hook = KedroTelemetryHook()
        telemetry_hook.after_context_created(fake_context)

        mocked_heap_call.assert_not_called()

    def test_old_catalog_with_list_method(self, pipeline_fixture, project_pipelines):
        # catalog.list() was replaces with catalog.keys() in `kedro >= 1.0`
        catalog = MagicMock()
        catalog.list.return_value = [
            "dataset1",
            "params:my_param",
            "dataset2",
            "parameters",
        ]

        # Ensure .keys is not present
        if hasattr(catalog, "keys"):
            del catalog.keys

        result = _format_project_statistics_data(
            catalog, pipeline_fixture, project_pipelines
        )

        ds_nodes_pipes_cnt = 2

        assert result["number_of_datasets"] == ds_nodes_pipes_cnt
        assert result["number_of_nodes"] == ds_nodes_pipes_cnt
        assert result["number_of_pipelines"] == ds_nodes_pipes_cnt

    def test_new_catalog_with_keys_method(self, pipeline_fixture, project_pipelines):
        # catalog.list() was replaces with catalog.keys() in `kedro >= 1.0`
        catalog = MagicMock()
        catalog.keys.return_value = [
            "datasetA",
            "params:global",
            "datasetB",
            "parameters",
        ]
        # Ensure .list is not present
        if hasattr(catalog, "list"):
            del catalog.list

        result = _format_project_statistics_data(
            catalog, pipeline_fixture, project_pipelines
        )

        ds_nodes_pipes_cnt = 2

        assert result["number_of_datasets"] == ds_nodes_pipes_cnt
        assert result["number_of_nodes"] == ds_nodes_pipes_cnt
        assert result["number_of_pipelines"] == ds_nodes_pipes_cnt
