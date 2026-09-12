"""命令行测试：参数解析、退出码与关键子命令。"""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from regwatch.cli.main import app

runner = CliRunner()


def _invoke(database: Path, args: list[str]):
    return runner.invoke(app, ["--database", str(database), *args])


def test_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.stdout.strip()


def test_help_lists_all_commands() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in ("fetch", "summarize", "report", "org-type", "config", "db", "web", "jobs"):
        assert command in result.stdout


def test_db_init_creates_file(tmp_path: Path) -> None:
    target = tmp_path / "cli.db"
    result = _invoke(target, ["db", "init"])
    assert result.exit_code == 0, result.output
    assert target.exists()


def test_db_stats_reports_zero_cases(tmp_path: Path) -> None:
    target = tmp_path / "cli.db"
    assert _invoke(target, ["db", "init"]).exit_code == 0
    result = _invoke(target, ["db", "stats"])
    assert result.exit_code == 0
    assert "0" in result.stdout


def test_db_import_rejects_missing_directory(tmp_path: Path) -> None:
    result = _invoke(tmp_path / "cli.db", ["db", "import", "--data-root", str(tmp_path / "nope")])
    assert result.exit_code == 1


def test_summarize_without_cases_is_a_noop(tmp_path: Path) -> None:
    result = _invoke(tmp_path / "cli.db", ["summarize", "--dataset", "all"])
    assert result.exit_code == 0, result.output


def test_summarize_rejects_bad_dataset(tmp_path: Path) -> None:
    result = _invoke(tmp_path / "cli.db", ["summarize", "--dataset", "nope"])
    assert result.exit_code == 1


def test_report_without_saving(tmp_path: Path) -> None:
    result = _invoke(tmp_path / "cli.db", ["report", "--dataset", "amac", "--no-save"])
    assert result.exit_code == 0, result.output


def test_org_type_dry_run(tmp_path: Path) -> None:
    result = _invoke(tmp_path / "cli.db", ["org-type", "--dry-run"])
    assert result.exit_code == 0, result.output


def test_config_show(tmp_path: Path) -> None:
    result = _invoke(tmp_path / "cli.db", ["config", "show"])
    assert result.exit_code == 0, result.output


def test_jobs_lists_empty(tmp_path: Path) -> None:
    result = _invoke(tmp_path / "cli.db", ["jobs"])
    assert result.exit_code == 0, result.output


def test_info_reports_environment(tmp_path: Path) -> None:
    result = _invoke(tmp_path / "cli.db", ["info"])
    assert result.exit_code == 0, result.output


def test_fetch_requires_known_subcommand(tmp_path: Path) -> None:
    result = _invoke(tmp_path / "cli.db", ["fetch"])
    assert result.exit_code != 0


def test_config_add_model_requires_id(tmp_path: Path) -> None:
    result = _invoke(tmp_path / "cli.db", ["config", "add-model"])
    assert result.exit_code != 0
